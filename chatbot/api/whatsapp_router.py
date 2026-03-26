import asyncio
import logging
import time
import traceback
from datetime import datetime

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic_ai import AgentRunResult
from pydantic_ai.exceptions import ModelAPIError, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart

from chatbot.ai_agent import get_cheese_agent
from chatbot.ai_agent.context import webhook_context_manager
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.error_agent import run_error_agent
from chatbot.ai_agent.summary_agent import summarize_conversation
from chatbot.ai_agent.tools.payments import (
    erp_validation_user_message,
    parse_amount,
    register_deposit_payment,
)
from chatbot.api.utils import message_handler
from chatbot.api.utils.message_queue import Message, message_queue
from chatbot.api.utils.text import strip_markdown
from chatbot.api.utils.webhook_parser import ParsedMessage, extract_message_content
from chatbot.core import human_control
from chatbot.core.config import config
from chatbot.db.services import services
from chatbot.erp.client import build_erp_client
from chatbot.erp.transcript import upload_message_transcript
from chatbot.messaging.telegram_notifier import notify_error, notify_slow_response
from chatbot.messaging.whatsapp import whatsapp_manager

logger = logging.getLogger(__name__)
load_dotenv()
router = APIRouter()
ERROR_STATUS = {"status": "error"}
OK_STATUS = {"status": "ok"}
USER_ERROR_MSG = "Ocurrio un error al procesar tu mensaje. Por favor intentalo de nuevo o escribe /restart para reiniciar el chat."
erp_client: httpx.AsyncClient = build_erp_client()

# Excepciones que indican un fallo transitorio del proveedor de IA y ameritan reintento
_PROVIDER_ERRORS = (ModelAPIError, httpx.TimeoutException, httpx.ConnectError)

SLOW_RESPONSE_THRESHOLD: float = 30.0
HISTORY_SUMMARY_THRESHOLD: int = 30


async def _maybe_compress_history(user_number: str, history_len: int) -> None:
    """Comprime el historial si tras el turno actual supera el umbral.

    Genera un resumen con summarize_conversation, borra el historial y persiste
    el resumen como mensaje de sistema para que el agente retome el contexto.
    """
    total = history_len + 2  # +1 usuario + 1 asistente del turno actual
    if total <= HISTORY_SUMMARY_THRESHOLD:
        return

    logger.info(
        "[history] Compressing history for %s (%d messages > %d threshold)",
        user_number,
        total,
        HISTORY_SUMMARY_THRESHOLD,
    )
    try:
        chat_str = await services.get_chat_str(user_number)
        summary = await summarize_conversation(chat_str)
        await services.reset_chat(user_number)
        await services.create_message(phone=user_number, role="system", message=summary)
        logger.info("[history] History compressed for %s", user_number)
    except Exception as exc:
        logger.error(
            "[history] Failed to compress history for %s: %s", user_number, exc
        )
        await notify_error(exc, context=f"_maybe_compress_history | user={user_number}")


@router.get("")
async def verify_webhook(request: Request):
    try:
        mode = request.query_params.get("hub.mode")
        challenge = request.query_params.get("hub.challenge")
        token = request.query_params.get("hub.verify_token")

        verify_token_expected = config.WHATSAPP_VERIFY_TOKEN

        if mode == "subscribe" and token == verify_token_expected:
            logger.info("WEBHOOK VERIFIED for Meta WhatsApp API")
            return PlainTextResponse(str(challenge))
        else:
            logger.warning(
                f"Webhook verification failed - Mode: {mode}, "
                f"Token match: {token == verify_token_expected}"
            )
            raise HTTPException(status_code=403, detail="Forbidden")
    except Exception as e:
        logger.error(f"Error in webhook verification: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


def _extract_tools_used(result: AgentRunResult[str]) -> list[str]:
    """Extract tool names called during the agent run."""
    tools: list[str] = []
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tools.append(part.tool_name)
    return tools


async def _process_message(message: Message) -> None:
    """Process a single message from the queue sequentially per user."""
    user_number = message.user_number
    incoming_msg = message.content
    message_id = message.message_id

    if not message_id:
        logger.error("No message_id provided for WhatsApp message")
        return

    await whatsapp_manager.mark_read(message_id)
    await whatsapp_manager.send_typing_indicator(message_id)

    # ------------------------------------------------------------------
    # Human-control check: skip AI if operator has taken over this chat
    # ------------------------------------------------------------------
    if human_control.is_whatsapp_controlled(user_number):
        logger.info(
            "[human-control] Skipping AI for %s — conversation under human control",
            user_number,
        )
        return

    try:
        if incoming_msg.lower() == "/restart":
            logger.info("'/restart' requested by %s", user_number)
            await services.reset_chat(user_number)
            await whatsapp_manager.send_text(
                user_number=user_number, text="Chat reiniciado", message_id=message_id
            )
            return

        logger.info("=" * 80)
        logger.info("%s: %s", user_number, incoming_msg)

        await message_handler.save_user_msg(user_number, incoming_msg)

        deps = AgentDeps(
            erp_client=erp_client,
            db_services=services,
            whatsapp_client=whatsapp_manager,
            webhook_context=webhook_context_manager,
            user_phone=user_number,
        )

        agent = get_cheese_agent()
        history = await services.get_pydantic_ai_history(user_number, hours=24)

        ai_response: str
        tools_used: list[str]
        message_datetime = datetime.now()
        provider_error: str | None = None

        try:
            agent_start = time.monotonic()
            try:
                result = await agent.run(
                    incoming_msg, deps=deps, message_history=history
                )
            except _PROVIDER_ERRORS as provider_exc:
                logger.warning(
                    "Provider error on first attempt for %s: %s. Retrying...",
                    user_number,
                    provider_exc,
                )
                provider_error = f"{type(provider_exc).__name__}: {provider_exc}"
                result = await agent.run(
                    incoming_msg, deps=deps, message_history=history
                )
            except UsageLimitExceeded as ule:
                logger.warning(
                    "UsageLimitExceeded for %s: %s. Summarizing history and retrying...",
                    user_number,
                    ule,
                )
                await notify_error(
                    ule,
                    context=f"_process_message | user={user_number} | msg={incoming_msg[:200]} | action=summary_retry",
                )
                chat_str = await services.get_chat_str(user_number)
                summary = await summarize_conversation(chat_str)
                await services.reset_chat(user_number)
                await services.create_message(
                    phone=user_number, role="system", message=summary
                )
                logger.info(
                    "[history] Summarized history and saved system message for %s",
                    user_number,
                )
                new_history = await services.get_pydantic_ai_history(
                    user_number, hours=24
                )
                result = await agent.run(
                    incoming_msg, deps=deps, message_history=new_history
                )

            response_time = time.monotonic() - agent_start
            ai_response = strip_markdown(result.output)
            tools_used = _extract_tools_used(result)

            if response_time > SLOW_RESPONSE_THRESHOLD:
                logger.warning(
                    "Slow response for %s: %.1fs", user_number, response_time
                )
                await notify_slow_response(
                    phone=user_number,
                    user_message=incoming_msg,
                    tools_used=tools_used,
                    ai_response=ai_response,
                    message_datetime=message_datetime,
                    history_count=len(history),
                    response_time=response_time,
                    provider_error=provider_error,
                )

        except Exception as agent_exc:
            logger.error(
                "Agent error for %s: %s", user_number, agent_exc, exc_info=True
            )
            await notify_error(
                agent_exc,
                context=f"_process_message | user={user_number} | msg={incoming_msg[:200]}",
            )
            try:
                explanation = await run_error_agent(traceback.format_exc())
                ai_response = explanation.user_message
            except Exception as explainer_exc:
                logger.error("Error agent also failed: %s", explainer_exc)
                ai_response = USER_ERROR_MSG
            tools_used = []

        logger.info("🤖 Agent response for %s: %s", user_number, ai_response)
        logger.info("🔧 Tools used: %s", tools_used)

        await message_handler.save_assistant_msg(user_number, ai_response, tools_used)
        await whatsapp_manager.send_text(
            user_number=user_number, text=ai_response, message_id=message_id
        )
        asyncio.create_task(_maybe_compress_history(user_number, len(history)))
        asyncio.create_task(
            upload_message_transcript(
                client=erp_client,
                phone_number=user_number,
                user_message=incoming_msg,
                bot_response=ai_response,
            )
        )

    except Exception as exc:
        logger.exception("Error processing message for %s: %s", user_number, exc)
        await notify_error(
            exc,
            context=f"_process_message | user={user_number} | msg={incoming_msg[:200]}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number, text=USER_ERROR_MSG, message_id=message_id
        )


@router.post("")
async def whatsapp_reply(request: Request, background_tasks: BackgroundTasks):
    logger.info("Received WhatsApp message webhook")
    try:
        webhook_data = await request.json()
    except Exception as exc:
        logger.error(f"Error parsing webhook data: {exc}")
        return ERROR_STATUS

    message_data = await extract_message_content(webhook_data)
    if not message_data:
        return OK_STATUS

    user_number = message_data.user_number
    message_id = message_data.message_id

    # --- Image receipt: OCR → ERP registration → notify user ---
    if message_data.receipt is not None:
        await whatsapp_manager.mark_read(message_id)
        background_tasks.add_task(
            _process_image_receipt,
            user_number=user_number,
            message_id=message_id,
            message_data=message_data,
        )
        return OK_STATUS

    # --- Text / audio: queue for agent processing ---
    incoming_msg = message_data.text or ""
    if not incoming_msg:
        return OK_STATUS

    msg = Message(user_number=user_number, content=incoming_msg, message_id=message_id)
    await message_queue.enqueue(msg)
    await message_queue.start_processing(user_number, _process_message)

    # Notify user if queue is building up
    queue_size = message_queue.queue_size(user_number)
    if queue_size > 1:
        logger.warning(f"Queue size for {user_number} is {queue_size}")

    return OK_STATUS


async def _process_image_receipt(
    user_number: str,
    message_id: str,
    message_data: ParsedMessage,
) -> None:
    """Register a payment receipt in the ERP and notify the user via WhatsApp."""
    receipt = message_data.receipt
    ticket_id = message_data.ticket_id

    if receipt is None:
        return

    # Validate required fields before hitting the ERP
    amount = parse_amount(receipt.amount)
    if amount is None:
        logger.error(
            "[receipt] Could not parse amount from receipt for user=%s amount_raw=%s",
            user_number,
            receipt.amount,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "No se pudo determinar el monto del comprobante. "
                "Por favor verifica la imagen e inténtalo de nuevo."
            ),
            message_id=message_id,
        )
        return

    if not ticket_id:
        logger.warning(
            "[receipt] No ticket_id in caption for user=%s — cannot register payment",
            user_number,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "Para registrar tu pago necesito el número de ticket (ej: TKT-2026-03-00018). "
                "Por favor envía la imagen con el número de ticket como descripción."
            ),
            message_id=message_id,
        )
        return

    ocr_payload = receipt.model_dump(exclude_none=True)
    try:
        result = await register_deposit_payment(
            erp_client=erp_client,
            ticket_id=ticket_id,
            amount=amount,
            ocr_payload=ocr_payload,
        )
    except ValueError as exc:
        user_msg = erp_validation_user_message(exc)
        if user_msg:
            logger.warning(
                "[receipt] ERP validation error for user=%s ticket=%s: %s",
                user_number,
                ticket_id,
                exc,
            )
            await whatsapp_manager.send_text(
                user_number=user_number,
                text=f"⚠️ {user_msg}",
                message_id=message_id,
            )
            return
        logger.error(
            "[receipt] Failed to register payment for user=%s ticket=%s: %s",
            user_number,
            ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=f"_process_image_receipt | user={user_number} | ticket={ticket_id}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "Ocurrió un error al registrar tu pago en el sistema. "
                "Por favor inténtalo de nuevo o escala tu solicitud a un humano."
            ),
            message_id=message_id,
        )
        return
    except Exception as exc:
        logger.error(
            "[receipt] Failed to register payment for user=%s ticket=%s: %s",
            user_number,
            ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=f"_process_image_receipt | user={user_number} | ticket={ticket_id}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "Ocurrió un error al registrar tu pago en el sistema. "
                "Por favor inténtalo de nuevo o escala tu solicitud a un humano."
            ),
            message_id=message_id,
        )
        return

    logger.info(
        "[receipt] Payment registered — deposit_id=%s ticket_id=%s amount_paid=%.2f "
        "amount_remaining=%.2f is_complete=%s",
        result.deposit_id,
        result.ticket_id,
        result.amount_paid,
        result.amount_remaining,
        result.is_complete,
    )
    if result.is_complete:
        msg = (
            f"✅ Pago registrado exitosamente.\n"
            f"Depósito: {result.deposit_id}\n"
            f"Monto pagado: {result.amount_paid}\n"
            f"Estado: Pago completado."
        )
    else:
        msg = (
            f"✅ Pago registrado exitosamente.\n"
            f"Depósito: {result.deposit_id}\n"
            f"Monto pagado: {result.amount_paid}\n"
            f"Monto restante: {result.amount_remaining}"
        )
    await whatsapp_manager.send_text(
        user_number=user_number, text=msg, message_id=message_id
    )

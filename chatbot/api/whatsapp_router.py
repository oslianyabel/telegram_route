import asyncio
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from pydantic_ai import AgentRunResult
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart

from chatbot.ai_agent import get_cheese_agent
from chatbot.ai_agent.agent import FALLBACK_MODEL
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.error_agent import run_error_agent
from chatbot.ai_agent.models import ERP_BASE_PATH, SurveyResult
from chatbot.ai_agent.summary_agent import summarize_conversation
from chatbot.ai_agent.tools.erp_utils import extract_erp_data
from chatbot.ai_agent.tools.ocr import (
    extract_payment_receipt,
    extract_payment_receipt_from_pdf,
)
from chatbot.ai_agent.tools.payments import (
    PendingDepositStatus,
    erp_validation_user_message,
    parse_amount,
    register_deposit_payment,
    user_has_pending_deposit,
    validate_ocr_against_bank_account,
    validate_ticket_ownership,
)
from chatbot.api.utils import message_handler
from chatbot.api.utils.message_queue import Message, message_queue
from chatbot.api.utils.qr import (
    build_qr_caption,
    build_qr_image_url,
    fetch_reservation_qr,
)
from chatbot.api.utils.survey_feedback import (
    clear_pending_survey,
    extract_survey_feedback,
    get_pending_survey,
)
from chatbot.api.utils.text import strip_markdown
from chatbot.api.utils.webhook_parser import (
    _TICKET_ID_RE,
    ParsedMessage,
    create_or_retrieve_images_dir,
    extract_message_content,
)
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
USER_ERROR_MSG = "An error occurred while processing your message. Please try again or send /restart to restart the chat."
erp_client: httpx.AsyncClient = build_erp_client()

# Excepciones que indican un fallo transitorio del proveedor de IA y ameritan reintento
_PROVIDER_ERRORS = (ModelAPIError, httpx.TimeoutException, httpx.ConnectError)


@dataclass
class _PendingReceipt:
    """Archivo descargado a la espera de que el usuario provea el ticket ID."""

    file_path: str
    is_pdf: bool


_pending_receipt: dict[str, _PendingReceipt] = {}

SLOW_RESPONSE_THRESHOLD: float = 30.0
HISTORY_SUMMARY_THRESHOLD: int = 30
ERP_TIMEOUT_SECONDS: float = 15.0


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


_PENDING_DEPOSIT_MESSAGES: dict[PendingDepositStatus, str] = {
    PendingDepositStatus.NO_CONFIRMED_TICKETS: (
        "You don't have any reservations in CONFIRMED status, so there is no pending payment to register."
    ),
    PendingDepositStatus.NO_PENDING_DEPOSITS: (
        "All your confirmed reservations are already fully paid. "
        "There is no outstanding deposit to register."
    ),
}


async def _store_pending_file(
    user_number: str,
    message_id: str,
    file_path: str,
    is_pdf: bool,
) -> None:
    """Verifica depósitos pendientes, guarda el archivo y pide al usuario el número de ticket."""
    deposit_status = await user_has_pending_deposit(
        erp_client=erp_client, user_phone=user_number
    )
    if deposit_status != PendingDepositStatus.HAS_PENDING:
        logger.info(
            "[receipt] User=%s deposit_status=%s — discarding file",
            user_number,
            deposit_status,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=_PENDING_DEPOSIT_MESSAGES[deposit_status],
            message_id=message_id,
        )
        return

    _pending_receipt[user_number] = _PendingReceipt(file_path=file_path, is_pdf=is_pdf)
    logger.info(
        "[receipt] Stored pending %s for user=%s, waiting for ticket_id",
        "PDF" if is_pdf else "image",
        user_number,
    )
    await whatsapp_manager.send_text(
        user_number=user_number,
        text=(
            "I received your payment receipt. 🧾\n"
            "Please send me the ticket number (for example: TKT-2026-03-00018) so I can register the payment:"
        ),
        message_id=message_id,
    )


async def _register_and_notify_payment(
    user_number: str,
    message_id: str,
    file_path: str,
    is_pdf: bool,
    ticket_id: str,
) -> None:
    """Ejecuta OCR sobre el archivo, valida la titularidad del ticket, registra el pago y notifica al usuario."""
    try:
        if is_pdf:
            receipt = await extract_payment_receipt_from_pdf(file_path)
        else:
            receipt = await extract_payment_receipt(file_path)
    except Exception as exc:
        logger.error(
            "[ocr] OCR failed for user=%s file=%s: %s", user_number, file_path, exc
        )
        await notify_error(
            exc,
            context=f"_register_and_notify_payment OCR | user={user_number} | file={file_path}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "The receipt could not be processed. "
                "Please check the file and try again."
            ),
            message_id=message_id,
        )
        return

    logger.info(
        "[ocr] Extracted receipt data — "
        "amount=%s | date=%s | reference=%s | account=%s | "
        "recipient_name=%s | payment_method=%s | branch=%s | concept=%s | "
        "bank_name=%s | currency=%s",
        receipt.amount,
        receipt.date,
        receipt.reference,
        receipt.account,
        receipt.recipient_name,
        receipt.payment_method,
        receipt.branch,
        receipt.concept,
        receipt.bank_name,
        receipt.currency,
    )

    amount = parse_amount(receipt.amount)
    if amount is None:
        logger.error(
            "[receipt] Could not parse amount for user=%s amount_raw=%s",
            user_number,
            receipt.amount,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "The receipt amount could not be determined. "
                "Please check the file and try again."
            ),
            message_id=message_id,
        )
        return

    try:
        await validate_ticket_ownership(
            erp_client=erp_client,
            user_phone=user_number,
            ticket_id=ticket_id,
        )
    except ValueError as exc:
        logger.warning(
            "[receipt] Ticket validation failed for user=%s ticket=%s: %s",
            user_number,
            ticket_id,
            exc,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=f"⚠️ {exc}",
            message_id=message_id,
        )
        return

    ocr_payload = receipt.model_dump(exclude_none=True)

    ocr_valid, ocr_reason = await validate_ocr_against_bank_account(
        erp_client=erp_client,
        ticket_id=ticket_id,
        receipt=receipt,
    )
    if not ocr_valid:
        logger.warning(
            "[receipt] OCR bank validation failed for user=%s ticket=%s: %s",
            user_number,
            ticket_id,
            ocr_reason,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=f"⚠️ {ocr_reason}",
            message_id=message_id,
        )
        return

    try:
        result = await register_deposit_payment(
            erp_client=erp_client,
            ticket_id=ticket_id,
            amount=amount,
            ocr_payload=ocr_payload,
            receipt_file_path=file_path,
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
            context=f"_register_and_notify_payment | user={user_number} | ticket={ticket_id}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "An error occurred while registering your payment in the system. "
                "Please try again or escalate your request to a human agent."
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
            context=f"_register_and_notify_payment | user={user_number} | ticket={ticket_id}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "An error occurred while registering your payment in the system. "
                "Please try again or escalate your request to a human agent."
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
            f"✅ Deposit fully paid!\n"
            f"Deposit: {result.deposit_id}\n"
            f"Ticket: {result.ticket_id}\n"
            f"Total paid: {result.total_amount_paid} UYU"
        )
        await whatsapp_manager.send_text(
            user_number=user_number, text=msg, message_id=message_id
        )
        await _fetch_and_send_qr(
            user_number=user_number,
            ticket_id=ticket_id,
            message_id=message_id,
        )
    else:
        msg = (
            f"✅ Payment registered successfully.\n"
            f"Deposit: {result.deposit_id}\n"
            f"Amount paid: {result.amount_paid} UYU\n"
            f"Amount remaining: {result.amount_remaining} UYU"
        )
        await whatsapp_manager.send_text(
            user_number=user_number, text=msg, message_id=message_id
        )


async def _handle_pending_survey_response(
    user_number: str,
    incoming_msg: str,
    message_id: str,
) -> bool:
    """Procesa la próxima respuesta del usuario si hay una encuesta de satisfacción pendiente."""
    pending_survey = get_pending_survey(user_number)
    if pending_survey is None:
        return False

    feedback = extract_survey_feedback(incoming_msg)
    if feedback is None:
        logger.info(
            "[survey] Message for user=%s did not look like survey feedback; releasing to main agent",
            user_number,
        )
        clear_pending_survey(user_number)
        return False

    await message_handler.save_user_msg(user_number, incoming_msg)

    payload: dict[str, Any] = {
        "ticket_id": pending_survey.ticket_id,
        "rating": feedback.rating,
    }
    if feedback.comment:
        payload["comment"] = feedback.comment

    try:
        response = await erp_client.post(
            f"{ERP_BASE_PATH}.survey_controller.submit_survey_response",
            json=payload,
            timeout=ERP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = extract_erp_data(response.json())
        result = SurveyResult.model_validate(data)
    except Exception as exc:
        clear_pending_survey(user_number)
        logger.error(
            "[survey] Failed to submit survey for user=%s ticket=%s: %s",
            user_number,
            pending_survey.ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=(
                f"_handle_pending_survey_response | user={user_number} | ticket={pending_survey.ticket_id}"
            ),
        )
        error_message = (
            "Thanks for your feedback. We had a problem saving it in the system, "
            "but we've already notified the team to review it."
        )
        await message_handler.save_assistant_msg(user_number, error_message, [])
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=error_message,
            message_id=message_id,
        )
        return True

    clear_pending_survey(user_number)
    thanks_message = (
        f"Thanks for your feedback. We recorded your rating of {result.rating}/5"
        f" for ticket {result.ticket_id}."
    )
    await message_handler.save_assistant_msg(user_number, thanks_message, [])
    await whatsapp_manager.send_text(
        user_number=user_number,
        text=thanks_message,
        message_id=message_id,
    )
    logger.info(
        "[survey] Survey stored for user=%s ticket=%s rating=%s",
        user_number,
        result.ticket_id,
        result.rating,
    )
    return True


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

    if await _handle_pending_survey_response(
        user_number=user_number,
        incoming_msg=incoming_msg,
        message_id=message_id,
    ):
        return

    # ------------------------------------------------------------------
    # Pending payment receipt — waiting for ticket ID from the user
    # ------------------------------------------------------------------
    if user_number in _pending_receipt:
        match = _TICKET_ID_RE.search(incoming_msg)
        if match:
            ticket_id_pending = match.group().upper()
            logger.info(
                "[receipt] Received ticket_id=%s for pending receipt of user=%s",
                ticket_id_pending,
                user_number,
            )
            pending = _pending_receipt.pop(user_number)
            await _register_and_notify_payment(
                user_number=user_number,
                message_id=message_id,
                file_path=pending.file_path,
                is_pdf=pending.is_pdf,
                ticket_id=ticket_id_pending,
            )
            return
        else:
            # No ticket_id found — stop waiting and pass the message to the AI agent
            _pending_receipt.pop(user_number, None)
            logger.info(
                "[receipt] No ticket_id found in message for user=%s — releasing to AI agent",
                user_number,
            )
            # Fall through to AI agent processing

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
                user_number=user_number, text="Chat restarted", message_id=message_id
            )
            return

        logger.info("=" * 80)
        logger.info("%s: %s", user_number, incoming_msg)

        await services.ensure_system_message(
            phone=user_number,
            message="CHANNEL: whatsapp",
        )
        await message_handler.save_user_msg(user_number, incoming_msg)

        async def _send_photo_wa(image_bytes: bytes, caption: str) -> None:
            media_id = await whatsapp_manager.upload_media_bytes(image_bytes)
            await whatsapp_manager.send_image_by_id(
                to=user_number, image_id=media_id, caption=caption
            )

        deps = AgentDeps(
            erp_client=erp_client,
            db_services=services,
            whatsapp_client=whatsapp_manager,
            user_phone=user_number,
            send_photo_callback=_send_photo_wa,
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
                if (
                    isinstance(provider_exc, ModelHTTPError)
                    and provider_exc.status_code == 503
                ):
                    logger.info(
                        "[fallback] 503 on primary model — switching to %s",
                        FALLBACK_MODEL,
                    )
                    result = await agent.run(
                        incoming_msg,
                        deps=deps,
                        message_history=history,
                        model=FALLBACK_MODEL,
                    )
                else:
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

    # --- Unsupported document format ---
    if message_data.unsupported_format:
        await whatsapp_manager.mark_read(message_id)
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "The file format is not supported. "
                "Please send your payment receipt as a JPG, PNG or PDF."
            ),
            message_id=message_id,
        )
        return OK_STATUS

    # --- Image/PDF with ticket in caption: OCR already done → register payment ---
    if message_data.receipt is not None:
        await whatsapp_manager.mark_read(message_id)
        background_tasks.add_task(
            _process_image_receipt,
            user_number=user_number,
            message_id=message_id,
            message_data=message_data,
        )
        return OK_STATUS

    # --- Image/PDF without ticket in caption: store file path, ask for ticket ---
    if message_data.media_file_path is not None:
        await whatsapp_manager.mark_read(message_id)
        background_tasks.add_task(
            _store_pending_file,
            user_number=user_number,
            message_id=message_id,
            file_path=message_data.media_file_path,
            is_pdf=message_data.is_pdf,
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
    """Llama a _register_and_notify_payment usando el ticket y el arquivo ya descargado.

    Este handler se usa cuando el ticket venía en el caption del mensaje — el OCR
    ya corrió en webhook_parser.py, y el media_file_path está disponible en message_data.
    """
    ticket_id = message_data.ticket_id
    file_path = message_data.media_file_path
    # receipt is set when OCR already ran (ticket was in caption)
    # re-use existing receipt to avoid running OCR again
    receipt = message_data.receipt
    if receipt is None or ticket_id is None or file_path is None:
        return

    deposit_status = await user_has_pending_deposit(
        erp_client=erp_client, user_phone=user_number
    )
    if deposit_status != PendingDepositStatus.HAS_PENDING:
        logger.info(
            "[receipt] User=%s deposit_status=%s — skipping registration",
            user_number,
            deposit_status,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=_PENDING_DEPOSIT_MESSAGES[deposit_status],
            message_id=message_id,
        )
        return

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
                "The receipt amount could not be determined. "
                "Please check the image and try again."
            ),
            message_id=message_id,
        )
        return

    try:
        await validate_ticket_ownership(
            erp_client=erp_client,
            user_phone=user_number,
            ticket_id=ticket_id,
        )
    except ValueError as exc:
        logger.warning(
            "[receipt] Ticket validation failed for user=%s ticket=%s: %s",
            user_number,
            ticket_id,
            exc,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=f"⚠️ {exc}",
            message_id=message_id,
        )
        return

    ocr_payload = receipt.model_dump(exclude_none=True)

    ocr_valid, ocr_reason = await validate_ocr_against_bank_account(
        erp_client=erp_client,
        ticket_id=ticket_id,
        receipt=receipt,
    )
    if not ocr_valid:
        logger.warning(
            "[receipt] OCR bank validation failed for user=%s ticket=%s: %s",
            user_number,
            ticket_id,
            ocr_reason,
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=f"⚠️ {ocr_reason}",
            message_id=message_id,
        )
        return

    try:
        result = await register_deposit_payment(
            erp_client=erp_client,
            ticket_id=ticket_id,
            amount=amount,
            ocr_payload=ocr_payload,
            receipt_file_path=file_path,
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
                "An error occurred while registering your payment in the system. "
                "Please try again or escalate your request to a human agent."
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
                "An error occurred while registering your payment in the system. "
                "Please try again or escalate your request to a human agent."
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
            f"✅ Deposit fully paid!\n"
            f"Deposit: {result.deposit_id}\n"
            f"Ticket: {result.ticket_id}\n"
            f"Total paid: {result.total_amount_paid} UYU"
        )
        await whatsapp_manager.send_text(
            user_number=user_number, text=msg, message_id=message_id
        )
        await _fetch_and_send_qr(
            user_number=user_number,
            ticket_id=ticket_id,
            message_id=message_id,
        )
    else:
        msg = (
            f"✅ Payment registered successfully.\n"
            f"Deposit: {result.deposit_id}\n"
            f"Amount paid: {result.amount_paid} UYU\n"
            f"Amount remaining: {result.amount_remaining} UYU"
        )
        await whatsapp_manager.send_text(
            user_number=user_number, text=msg, message_id=message_id
        )


async def _fetch_and_send_qr(
    user_number: str,
    ticket_id: str,
    message_id: str | None = None,
) -> None:
    """Obtiene el QR de check-in del ERP y lo envía al usuario por WhatsApp.

    Args:
        user_number: Número de WhatsApp del usuario.
        ticket_id: Identificador del ticket para el que se solicita el QR.
    """
    logger.info("[_fetch_and_send_qr] user=%s ticket_id=%s", user_number, ticket_id)
    try:
        qr_data = await fetch_reservation_qr(erp_client=erp_client, ticket_id=ticket_id)
        qr_image_url = build_qr_image_url(qr_data.qr_image_url)
        caption = build_qr_caption(ticket_id=qr_data.ticket_id, token=qr_data.token)
        # Upload the image to Meta Media API so it is accessible by Meta's servers.
        # Sending the ERP URL directly fails silently when the ERP is on a private network.
        safe_phone = user_number.replace("+", "").replace(" ", "")
        save_path = create_or_retrieve_images_dir() / f"{safe_phone}.png"
        media_id = await whatsapp_manager.upload_media(
            qr_image_url, save_path=save_path
        )
        sent = await whatsapp_manager.send_image_by_id(
            to=user_number,
            image_id=media_id,
            caption=caption,
            message_id=message_id,
        )
        if not sent:
            raise RuntimeError("WhatsApp image delivery failed")
        logger.info(
            "[qr] QR sent to WhatsApp user=%s ticket_id=%s qr_token_id=%s",
            user_number,
            qr_data.ticket_id,
            qr_data.qr_token_id,
        )
    except (httpx.HTTPError, ValidationError, ValueError, RuntimeError) as exc:
        logger.error(
            "[qr] Failed to fetch/send QR for user=%s ticket_id=%s: %s",
            user_number,
            ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=f"_fetch_and_send_qr | user={user_number} | ticket={ticket_id}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number,
            text=(
                "Your payment was completed, but I couldn't send your check-in QR right now. "
                "Please contact a human agent so they can share it with you."
            ),
            message_id=message_id,
        )

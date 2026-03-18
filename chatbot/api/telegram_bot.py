"""Telegram chatbot entry point using the Ruta del Queso AI agent.

On first interaction (or via /start) the user is asked to provide their phone
number, which is stored in ``_user_phones`` (keyed by Telegram chat_id).
That phone is then passed as ``user_phone`` in AgentDeps on every AI call.
The Telegram chat_id is used as the conversation identifier in the DB and as
``telegram_id`` in AgentDeps.

Run with:
    uv run python scripts/run_telegram_bot.py
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from chatbot.ai_agent import get_cheese_agent
from chatbot.ai_agent.context import webhook_context_manager
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.error_agent import run_error_agent
from chatbot.ai_agent.tools.ocr import extract_payment_receipt
from chatbot.ai_agent.tools.payments import (
    erp_validation_user_message,
    parse_amount,
    register_deposit_payment,
)
from chatbot.api.utils import message_handler, telegram_commands
from chatbot.api.utils.telegram_commands import (
    cmd_get_availability,
    cmd_get_establishment_details,
    cmd_get_experience_detail,
    cmd_get_route_availability,
    cmd_get_route_detail,
    cmd_list_establishments,
    cmd_list_experiences,
    cmd_list_routes,
    cmd_resolve_or_create_contact,
    cmd_update_contact,
    cmd_upsert_lead,
)
from chatbot.api.utils.text import strip_markdown
from chatbot.api.utils.webhook_parser import (
    _TICKET_ID_RE,
    create_or_retrieve_images_dir,
)
from chatbot.core.config import config
from chatbot.core.logging_conf import init_logging
from chatbot.db.services import services
from chatbot.erp.client import build_erp_client
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ERP client — created in post_init, closed in post_shutdown
# ---------------------------------------------------------------------------
erp_client: httpx.AsyncClient | None = None

# ---------------------------------------------------------------------------
# Per-user phone registry
# Keys: Telegram chat_id (str). Values: validated phone number (str).
# _pending_phone: chat_ids waiting to provide their phone number.
# ---------------------------------------------------------------------------
_user_phones: dict[str, str] = {}
_pending_phone: set[str] = set()

# Stub WhatsApp client (not used in Telegram, but AgentDeps requires it)
_noop_whatsapp = WhatsAppClient()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _typing_loop(bot, chat_id: int) -> None:
    """Send 'typing' chat action every 4 s until the task is cancelled."""
    while True:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        await asyncio.sleep(4)


def _extract_tools_used(result) -> list[str]:
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    tools: list[str] = []
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tools.append(part.tool_name)
    return tools


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome message on /start."""
    if not update.message or not update.effective_chat:
        return
    user = update.effective_user
    chat_id = str(update.effective_chat.id)

    if chat_id not in _user_phones:
        _pending_phone.add(chat_id)
        await update.message.reply_text(
            f"¡Hola{', ' + user.first_name if user else ''}! 🧀\n"
            "Soy el asistente de Ruta del Queso.\n\n"
            "Antes de comenzar necesito tu número de teléfono (con código de país, ej: +59899000000):"
        )
    else:
        await update.message.reply_text(
            f"¡Hola{', ' + user.first_name if user else ''}! 🧀\n"
            "Soy el asistente de Ruta del Queso. ¿En qué te puedo ayudar hoy?"
        )


async def _handle_change_phone(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/change_phone allows the user to update their registered phone number."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    _pending_phone.add(chat_id)
    await update.message.reply_text(
        "Por favor ingresa tu nuevo número de teléfono (con código de país, ej: +59899000000):"
    )
    logger.info("'/change_phone' requested by telegram_id=%s", chat_id)


async def _handle_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/restart clears the conversation history."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    await services.reset_chat(chat_id)
    await update.message.reply_text("Chat reiniciado. ¿En qué te puedo ayudar?")
    logger.info("'/restart' requested by telegram_id=%s", chat_id)


async def _handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Descarga la imagen recibida, ejecuta OCR, registra el pago en el ERP y notifica al usuario."""
    if not update.message or not update.message.photo or not update.effective_chat:
        return

    chat_id: str = str(update.effective_chat.id)

    try:
        photo = update.message.photo[-1]  # mayor resolución disponible
        tg_file = await photo.get_file()

        ext: str = ".jpg"
        if tg_file.file_path:
            suffix = tg_file.file_path.rsplit(".", 1)[-1].lower()
            if suffix in {"jpg", "jpeg", "png", "webp"}:
                ext = f".{suffix}"

        images_dir = create_or_retrieve_images_dir()
        file_path = images_dir / f"{chat_id}{ext}"
        await tg_file.download_to_drive(str(file_path))
        logger.info("[image] Saved Telegram image to %s", file_path)

        # Extract ticket_id from caption if present
        caption: str | None = update.message.caption
        ticket_id: str | None = None
        if caption:
            match = _TICKET_ID_RE.search(caption)
            if match:
                ticket_id = match.group().upper()
                logger.info("[image] Extracted ticket_id=%s from caption", ticket_id)
            else:
                logger.debug("[image] Caption present but no ticket_id: %r", caption)

        typing_task = asyncio.create_task(
            _typing_loop(context.bot, update.effective_chat.id)
        )
        try:
            receipt = await extract_payment_receipt(str(file_path))
        finally:
            typing_task.cancel()

        # Log all extracted OCR fields
        logger.info(
            "[ocr] Extracted receipt data — "
            "amount=%s | date=%s | reference=%s | account=%s | "
            "recipient_name=%s | payment_method=%s | branch=%s | concept=%s",
            receipt.amount,
            receipt.date,
            receipt.reference,
            receipt.account,
            receipt.recipient_name,
            receipt.payment_method,
            receipt.branch,
            receipt.concept,
        )

        # Validate amount
        amount = parse_amount(receipt.amount)
        if amount is None:
            logger.error(
                "[receipt] Could not parse amount from receipt for telegram_id=%s amount_raw=%s",
                chat_id,
                receipt.amount,
            )
            await update.message.reply_text(
                "No se pudo determinar el monto del comprobante. "
                "Por favor verifica la imagen e inténtalo de nuevo."
            )
            return

        if not ticket_id:
            logger.warning(
                "[receipt] No ticket_id in caption for telegram_id=%s", chat_id
            )
            await update.message.reply_text(
                "Para registrar tu pago necesito el número de ticket (ej: TKT-2026-03-00018). "
                "Por favor envía la imagen con el número de ticket como descripción."
            )
            return

        assert erp_client is not None, "ERP client not initialized"
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
                    "[receipt] ERP validation error for telegram_id=%s ticket=%s: %s",
                    chat_id,
                    ticket_id,
                    exc,
                )
                await update.message.reply_text(f"⚠️ {user_msg}")
                return
            raise

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
            reply = (
                f"✅ Pago registrado exitosamente.\n"
                f"Depósito: {result.deposit_id}\n"
                f"Monto pagado: {result.amount_paid}\n"
                f"Estado: Pago completado."
            )
        else:
            reply = (
                f"✅ Pago registrado exitosamente.\n"
                f"Depósito: {result.deposit_id}\n"
                f"Monto pagado: {result.amount_paid}\n"
                f"Monto restante: {result.amount_remaining}"
            )
        await update.message.reply_text(reply)

    except Exception as exc:
        logger.exception("Error processing image for telegram_id=%s: %s", chat_id, exc)
        await notify_error(
            exc,
            context=f"telegram_bot._handle_image | chat_id={chat_id}",
        )
        await update.message.reply_text(
            "Ocurrió un error al registrar el pago. Por favor inténtalo de nuevo."
        )


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process an incoming text message through the AI agent."""
    from telegram.error import TimedOut

    if not update.message or not update.message.text or not update.effective_chat:
        return

    chat_id_int: int = update.effective_chat.id
    chat_id: str = str(chat_id_int)
    incoming_msg: str = update.message.text

    try:
        # ------------------------------------------------------------------
        # Phone number collection — required before any AI interaction
        # ------------------------------------------------------------------
        if chat_id in _pending_phone:
            # User is responding to the phone request
            phone = incoming_msg.strip()
            _user_phones[chat_id] = phone
            _pending_phone.discard(chat_id)
            logger.info("Phone registered for telegram_id=%s: %s", chat_id, phone)
            await update.message.reply_text(
                f"¡Perfecto! Tu número {phone} fue registrado. ¿En qué te puedo ayudar hoy? 🧀"
            )
            return

        if chat_id not in _user_phones:
            # First interaction — ask for phone before anything else
            _pending_phone.add(chat_id)
            await update.message.reply_text(
                "Antes de continuar necesito tu número de teléfono "
                "(con código de país, ej: +59899000000):"
            )
            return

        logger.info("=" * 80)
        logger.info("telegram_id=%s: %s", chat_id, incoming_msg)

        # Start typing indicator loop in background
        typing_task = asyncio.create_task(_typing_loop(context.bot, chat_id_int))

        try:
            await message_handler.save_user_msg(chat_id, incoming_msg)

            assert erp_client is not None, "ERP client not initialized"
            deps = AgentDeps(
                erp_client=erp_client,
                db_services=services,
                whatsapp_client=_noop_whatsapp,
                webhook_context=webhook_context_manager,
                user_phone=_user_phones.get(chat_id, ""),
                telegram_id=chat_id,
            )

            agent = get_cheese_agent()
            history = await services.get_pydantic_ai_history(chat_id, hours=24)
            try:
                result = await agent.run(
                    incoming_msg, deps=deps, message_history=history
                )
                ai_response: str = strip_markdown(result.output)
                tools_used = _extract_tools_used(result)
            except Exception as agent_exc:
                logger.error(
                    "Agent error for telegram_id=%s: %s",
                    chat_id,
                    agent_exc,
                    exc_info=True,
                )
                await notify_error(
                    agent_exc,
                    context=f"_process_message | user={chat_id} | msg={incoming_msg[:200]}",
                )
                try:
                    explanation = await run_error_agent(str(agent_exc))
                    ai_response = explanation.user_message
                except Exception as explainer_exc:
                    logger.error("Error agent also failed: %s", explainer_exc)
                    ai_response = (
                        "Ocurrió un error al procesar tu mensaje. "
                        "Por favor inténtalo de nuevo o escribe /restart."
                    )
                tools_used = []

            logger.info("Agent response for telegram_id=%s: %s", chat_id, ai_response)
            logger.debug("Tools used: %s", tools_used)

            await message_handler.save_assistant_msg(chat_id, ai_response, tools_used)
            await update.message.reply_text(ai_response)

        finally:
            typing_task.cancel()

    except TimedOut:
        logger.warning(
            "Telegram TimedOut for telegram_id=%s — message dropped", chat_id
        )
    except Exception as exc:
        logger.exception("Error processing Telegram message for %s: %s", chat_id, exc)
        await notify_error(
            exc,
            context=f"telegram_bot._handle_message | chat_id={chat_id} | msg={incoming_msg[:80]}",
        )
        await update.message.reply_text(
            "Ocurrió un error al procesar tu mensaje. "
            "Por favor inténtalo de nuevo o escribe /restart para reiniciar el chat."
        )


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


async def _post_init(application: Application) -> None:
    """Connect to DB and create ERP client on startup."""
    global erp_client
    init_logging()
    logger.info("🤖 Telegram bot starting up")
    await services.database.connect()
    erp_client = build_erp_client()
    telegram_commands.init(erp_client)
    logger.info("✅ DB connected and ERP client ready")


async def _post_shutdown(application: Application) -> None:
    """Disconnect DB and close ERP client on shutdown."""
    global erp_client
    try:
        await services.database.disconnect()
        logger.info("✅ DB disconnected")
    except Exception as exc:
        logger.error("Error disconnecting DB: %s", exc)

    if erp_client:
        try:
            await erp_client.aclose()
            logger.info("✅ ERP client closed")
        except Exception as exc:
            logger.error("Error closing ERP client: %s", exc)


def build_application() -> Application:
    """Build and return the configured PTB Application."""
    if not config.TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured in .env")

    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", _handle_start))
    app.add_handler(CommandHandler("restart", _handle_restart))
    app.add_handler(CommandHandler("change_phone", _handle_change_phone))

    # Direct tool commands — bypass AI agent
    app.add_handler(CommandHandler("list_experiences", cmd_list_experiences))
    app.add_handler(CommandHandler("get_experience_detail", cmd_get_experience_detail))
    app.add_handler(CommandHandler("list_routes", cmd_list_routes))
    app.add_handler(CommandHandler("get_route_detail", cmd_get_route_detail))
    app.add_handler(CommandHandler("list_establishments", cmd_list_establishments))
    app.add_handler(
        CommandHandler("get_establishment_details", cmd_get_establishment_details)
    )
    app.add_handler(CommandHandler("get_availability", cmd_get_availability))
    app.add_handler(
        CommandHandler("get_route_availability", cmd_get_route_availability)
    )
    app.add_handler(
        CommandHandler("resolve_or_create_contact", cmd_resolve_or_create_contact)
    )
    app.add_handler(CommandHandler("update_contact", cmd_update_contact))
    app.add_handler(CommandHandler("upsert_lead", cmd_upsert_lead))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, _handle_image))

    return app

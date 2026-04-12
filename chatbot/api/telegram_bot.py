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
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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
from chatbot.api.utils import message_handler, telegram_commands
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
from chatbot.api.utils.telegram_commands import (
    cmd_cancel_reservation,
    cmd_get_availability,
    cmd_get_establishment_details,
    cmd_get_experience_detail,
    cmd_get_itinerary,
    cmd_get_phone,
    cmd_get_reservation_status,
    cmd_get_reservations,
    cmd_get_route_availability,
    cmd_get_route_booking_status,
    cmd_get_route_detail,
    cmd_list_available_experiences,
    cmd_list_establishments,
    cmd_list_experiences,
    cmd_list_routes,
    cmd_resolve_or_create_contact,
    cmd_start_followups,
    cmd_stop_followups,
    cmd_test_dev_notifications,
    cmd_update_contact,
    cmd_upsert_lead,
)
from chatbot.api.utils.text import strip_markdown
from chatbot.api.utils.webhook_parser import (
    _TICKET_ID_RE,
    create_or_retrieve_images_dir,
)
from chatbot.core import human_control
from chatbot.core.config import config
from chatbot.core.logging_conf import init_logging
from chatbot.db.services import services
from chatbot.erp.client import build_erp_client
from chatbot.erp.transcript import upload_message_transcript
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.whatsapp import WhatsAppManager
from chatbot.reminders.lead_followup import CHANNEL_MARKERS, CHANNEL_TELEGRAM

logger = logging.getLogger(__name__)

HISTORY_SUMMARY_THRESHOLD: int = 30
ERP_TIMEOUT_SECONDS: float = 15.0

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


@dataclass
class _PendingReceipt:
    """Archivo descargado a la espera de que el usuario provea el ticket ID."""

    file_path: str
    is_pdf: bool


_pending_receipt: dict[str, _PendingReceipt] = {}

# Stub WhatsApp client (not used in Telegram, but AgentDeps requires it)
_noop_whatsapp = WhatsAppManager()

_PENDING_DEPOSIT_MESSAGES: dict[PendingDepositStatus, str] = {
    PendingDepositStatus.NO_CONFIRMED_TICKETS: (
        "You don't have any reservations in CONFIRMED status, so there is no pending payment to register."
    ),
    PendingDepositStatus.NO_PENDING_DEPOSITS: (
        "All your confirmed reservations are already fully paid. "
        "There is no outstanding deposit to register."
    ),
}


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


async def _maybe_compress_history(chat_id: str, history_len: int) -> None:
    """Compress conversation history for Telegram when it exceeds threshold.

    Generates a summary, clears the stored history and saves the summary as
    a system message so the agent preserves context without the long history.
    """
    total = history_len + 2  # +1 user +1 assistant for the current turn
    if total <= HISTORY_SUMMARY_THRESHOLD:
        return

    logger.info(
        "[history] Compressing history for telegram_id=%s (%d messages > %d threshold)",
        chat_id,
        total,
        HISTORY_SUMMARY_THRESHOLD,
    )
    try:
        chat_str = await services.get_chat_str(chat_id)
        summary = await summarize_conversation(chat_str)
        await services.reset_chat(chat_id)
        await services.create_message(phone=chat_id, role="system", message=summary)
        logger.info("[history] History compressed for %s", chat_id)
    except Exception as exc:
        logger.error("[history] Failed to compress history for %s: %s", chat_id, exc)
        await notify_error(exc, context=f"_maybe_compress_history | chat_id={chat_id}")


async def _complete_payment(
    chat_id: str,
    message: Message,
    file_path: str,
    is_pdf: bool,
    ticket_id: str,
) -> None:
    """Ejecuta OCR sobre el archivo, valida la titularidad del ticket, registra el pago y notifica al usuario."""
    assert erp_client is not None, "ERP client not initialized"
    user_phone: str = _user_phones.get(chat_id, "")

    deposit_status = await user_has_pending_deposit(
        erp_client=erp_client, user_phone=user_phone
    )
    if deposit_status != PendingDepositStatus.HAS_PENDING:
        logger.info(
            "[receipt] User=%s deposit_status=%s — skipping registration",
            chat_id,
            deposit_status,
        )
        await message.reply_text(
            _PENDING_DEPOSIT_MESSAGES[deposit_status],
            do_quote=True,
        )
        return

    try:
        if is_pdf:
            receipt = await extract_payment_receipt_from_pdf(file_path)
        else:
            receipt = await extract_payment_receipt(file_path)
    except Exception as exc:
        logger.error(
            "[ocr] OCR failed for telegram_id=%s file=%s: %s", chat_id, file_path, exc
        )
        await message.reply_text(
            "The receipt could not be processed. Please try again.",
            do_quote=True,
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
            "[receipt] Could not parse amount from receipt for telegram_id=%s amount_raw=%s",
            chat_id,
            receipt.amount,
        )
        await message.reply_text(
            "The receipt amount could not be determined. "
            "Please check the file and try again.",
            do_quote=True,
        )
        return

    try:
        await validate_ticket_ownership(
            erp_client=erp_client,
            user_phone=user_phone,
            ticket_id=ticket_id,
        )
    except ValueError as exc:
        logger.warning(
            "[receipt] Ticket validation failed for telegram_id=%s ticket=%s: %s",
            chat_id,
            ticket_id,
            exc,
        )
        await message.reply_text(f"⚠️ {exc}", do_quote=True)
        return

    ocr_payload = receipt.model_dump(exclude_none=True)

    ocr_valid, ocr_reason = await validate_ocr_against_bank_account(
        erp_client=erp_client,
        ticket_id=ticket_id,
        receipt=receipt,
    )
    if not ocr_valid:
        logger.warning(
            "[receipt] OCR bank validation failed for telegram_id=%s ticket=%s: %s",
            chat_id,
            ticket_id,
            ocr_reason,
        )
        await message.reply_text(f"⚠️ {ocr_reason}", do_quote=True)
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
                "[receipt] ERP validation error for telegram_id=%s ticket=%s: %s",
                chat_id,
                ticket_id,
                exc,
            )
            await message.reply_text(f"⚠️ {user_msg}", do_quote=True)
            return
        logger.error(
            "[receipt] Failed to register payment for telegram_id=%s ticket=%s: %s",
            chat_id,
            ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=f"_complete_payment | chat_id={chat_id} | ticket={ticket_id}",
        )
        await message.reply_text(
            "An error occurred while registering your payment in the system. "
            "Please try again or escalate your request to a human agent.",
            do_quote=True,
        )
        return
    except Exception as exc:
        logger.error(
            "[receipt] Failed to register payment for telegram_id=%s ticket=%s: %s",
            chat_id,
            ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=f"_complete_payment | chat_id={chat_id} | ticket={ticket_id}",
        )
        await message.reply_text(
            "An error occurred while registering your payment in the system. "
            "Please try again or escalate your request to a human agent.",
            do_quote=True,
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
        reply = (
            f"✅ Payment registered successfully.\n"
            f"Deposit: {result.deposit_id}\n"
            f"Amount paid: {result.amount_paid}\n"
            f"Status: Payment completed."
        )
    else:
        reply = (
            f"✅ Payment registered successfully.\n"
            f"Deposit: {result.deposit_id}\n"
            f"Amount paid: {result.amount_paid}\n"
            f"Amount remaining: {result.amount_remaining}"
        )
    await message.reply_text(reply, do_quote=True)
    if result.is_complete:
        await _fetch_and_send_qr(chat_id=chat_id, message=message, ticket_id=ticket_id)


async def _fetch_and_send_qr(chat_id: str, message: Message, ticket_id: str) -> None:
    """Obtiene el QR de check-in del ERP y lo envía al usuario por Telegram."""
    assert erp_client is not None, "ERP client not initialized"

    try:
        qr_data = await fetch_reservation_qr(erp_client=erp_client, ticket_id=ticket_id)
        qr_image_url = build_qr_image_url(qr_data.qr_image_url)
        caption = build_qr_caption(ticket_id=qr_data.ticket_id, token=qr_data.token)
        # Download the image on the server side: Telegram's API also fetches URLs
        # server-side, which fails silently when the ERP is on a private network.
        async with httpx.AsyncClient(timeout=15.0) as client:
            img_resp = await client.get(qr_image_url)
            img_resp.raise_for_status()
            image_bytes = img_resp.content
        await message.reply_photo(photo=image_bytes, caption=caption, do_quote=True)
        logger.info(
            "[qr] QR sent to Telegram chat_id=%s ticket_id=%s qr_token_id=%s",
            chat_id,
            qr_data.ticket_id,
            qr_data.qr_token_id,
        )
    except (
        httpx.HTTPError,
        TelegramError,
        ValidationError,
        ValueError,
    ) as exc:
        logger.error(
            "[qr] Failed to fetch/send QR for telegram_id=%s ticket_id=%s: %s",
            chat_id,
            ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=f"telegram_bot._fetch_and_send_qr | chat_id={chat_id} | ticket={ticket_id}",
        )
        await message.reply_text(
            "Your payment was completed, but I couldn't send your check-in QR right now. "
            "Please contact a human agent so they can share it with you.",
            do_quote=True,
        )


async def _handle_pending_survey_response(
    chat_id: str,
    incoming_msg: str,
    message: Message,
) -> bool:
    """Procesa la próxima respuesta si el chat tiene una encuesta pendiente."""
    pending_survey = get_pending_survey(chat_id)
    if pending_survey is None:
        return False

    feedback = extract_survey_feedback(incoming_msg)
    if feedback is None:
        logger.info(
            "[survey] Message for telegram_id=%s did not look like survey feedback; releasing to main agent",
            chat_id,
        )
        clear_pending_survey(chat_id)
        return False

    await message_handler.save_user_msg(chat_id, incoming_msg)

    payload: dict[str, Any] = {
        "ticket_id": pending_survey.ticket_id,
        "rating": feedback.rating,
    }
    if feedback.comment:
        payload["comment"] = feedback.comment

    assert erp_client is not None, "ERP client not initialized"
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
        clear_pending_survey(chat_id)
        logger.error(
            "[survey] Failed to submit survey for telegram_id=%s ticket=%s: %s",
            chat_id,
            pending_survey.ticket_id,
            exc,
            exc_info=True,
        )
        await notify_error(
            exc,
            context=(
                f"telegram_bot._handle_pending_survey_response | chat_id={chat_id} | ticket={pending_survey.ticket_id}"
            ),
        )
        error_message = (
            "Thanks for your feedback. We had a problem saving it in the system, "
            "but we've already notified the team to review it."
        )
        await message_handler.save_assistant_msg(chat_id, error_message, [])
        await message.reply_text(error_message, do_quote=True)
        return True

    clear_pending_survey(chat_id)
    thanks_message = (
        f"Thanks for your feedback. We recorded your rating of {result.rating}/5"
        f" for ticket {result.ticket_id}."
    )
    await message_handler.save_assistant_msg(chat_id, thanks_message, [])
    await message.reply_text(thanks_message, do_quote=True)
    logger.info(
        "[survey] Survey stored for telegram_id=%s ticket=%s rating=%s",
        chat_id,
        result.ticket_id,
        result.rating,
    )
    return True


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
            f"Hello{', ' + user.first_name if user else ''}! 🧀\n"
            "I'm the Ruta del Queso assistant.\n\n"
            "Before we begin, I need your phone number (including country code, for example: +59899000000):",
            do_quote=True,
        )
    else:
        await update.message.reply_text(
            f"Hello{', ' + user.first_name if user else ''}! 🧀\n"
            "I'm the Ruta del Queso assistant. How can I help you today?",
            do_quote=True,
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
        "Please enter your new phone number (including country code, for example: +59899000000):",
        do_quote=True,
    )
    logger.info("'/change_phone' requested by telegram_id=%s", chat_id)


async def _handle_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/restart clears the conversation history."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    _pending_receipt.pop(chat_id, None)
    await services.reset_chat(chat_id)
    await update.message.reply_text(
        "Chat restarted. How can I help you?", do_quote=True
    )
    logger.info("'/restart' requested by telegram_id=%s", chat_id)


async def _handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cancelar clears any pending payment receipt state."""
    if not update.message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    had_pending = _pending_receipt.pop(chat_id, None) is not None
    if had_pending:
        await update.message.reply_text(
            "Payment registration cancelled. What else can I help you with?",
            do_quote=True,
        )
        logger.info("'/cancelar' cleared pending receipt for telegram_id=%s", chat_id)
    else:
        await update.message.reply_text(
            "There is nothing to cancel right now.",
            do_quote=True,
        )
        logger.info(
            "'/cancelar' requested with no pending state for telegram_id=%s", chat_id
        )


async def _handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Descarga la imagen recibida. Si el caption contiene el ticket, ejecuta OCR y registra el pago.
    En caso contrario guarda la ruta del archivo y espera el ticket en el próximo mensaje."""
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

        if not ticket_id:
            assert erp_client is not None, "ERP client not initialized"
            user_phone_img: str = _user_phones.get(chat_id, "")
            deposit_status = await user_has_pending_deposit(
                erp_client=erp_client, user_phone=user_phone_img
            )
            if deposit_status != PendingDepositStatus.HAS_PENDING:
                logger.info(
                    "[receipt] User=%s deposit_status=%s — discarding image",
                    chat_id,
                    deposit_status,
                )
                await update.message.reply_text(
                    _PENDING_DEPOSIT_MESSAGES[deposit_status],
                    do_quote=True,
                )
                return

            # Store file path and wait for ticket in the next message
            _pending_receipt[chat_id] = _PendingReceipt(
                file_path=str(file_path), is_pdf=False
            )
            logger.info(
                "[receipt] Stored pending image for telegram_id=%s, waiting for ticket_id",
                chat_id,
            )
            await update.message.reply_text(
                "I received your payment receipt. 🧾\n"
                "Please send me the ticket number (for example: TKT-2026-03-00018) so I can register the payment:",
                do_quote=True,
            )
            return

        # Ticket in caption — run OCR now and complete payment
        typing_task = asyncio.create_task(
            _typing_loop(context.bot, update.effective_chat.id)
        )
        try:
            await _complete_payment(
                chat_id=chat_id,
                message=update.message,
                file_path=str(file_path),
                is_pdf=False,
                ticket_id=ticket_id,
            )
        finally:
            typing_task.cancel()

    except Exception as exc:
        logger.exception("Error processing image for telegram_id=%s: %s", chat_id, exc)
        await notify_error(
            exc,
            context=f"telegram_bot._handle_image | chat_id={chat_id}",
        )
        await update.message.reply_text(
            "An error occurred while registering the payment. Please try again.",
            do_quote=True,
        )


async def _handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Descarga el PDF recibido. Si el caption contiene el ticket, ejecuta OCR y registra el pago.
    En caso contrario guarda la ruta del archivo y espera el ticket en el próximo mensaje."""
    if not update.message or not update.message.document or not update.effective_chat:
        return

    chat_id: str = str(update.effective_chat.id)

    try:
        document = update.message.document
        tg_file = await document.get_file()

        documents_dir = create_or_retrieve_images_dir().parent / "documents"
        documents_dir.mkdir(parents=True, exist_ok=True)
        file_path = documents_dir / f"{chat_id}.pdf"
        await tg_file.download_to_drive(str(file_path))
        logger.info("[pdf] Saved Telegram PDF to %s", file_path)

        # Extract ticket_id from caption if present
        caption: str | None = update.message.caption
        ticket_id: str | None = None
        if caption:
            match = _TICKET_ID_RE.search(caption)
            if match:
                ticket_id = match.group().upper()
                logger.info("[pdf] Extracted ticket_id=%s from caption", ticket_id)
            else:
                logger.debug("[pdf] Caption present but no ticket_id: %r", caption)

        if not ticket_id:
            assert erp_client is not None, "ERP client not initialized"
            user_phone_doc: str = _user_phones.get(chat_id, "")
            deposit_status = await user_has_pending_deposit(
                erp_client=erp_client, user_phone=user_phone_doc
            )
            if deposit_status != PendingDepositStatus.HAS_PENDING:
                logger.info(
                    "[receipt] User=%s deposit_status=%s — discarding PDF",
                    chat_id,
                    deposit_status,
                )
                await update.message.reply_text(
                    _PENDING_DEPOSIT_MESSAGES[deposit_status],
                    do_quote=True,
                )
                return

            # Store file path and wait for ticket in the next message
            _pending_receipt[chat_id] = _PendingReceipt(
                file_path=str(file_path), is_pdf=True
            )
            logger.info(
                "[receipt] Stored pending PDF for telegram_id=%s, waiting for ticket_id",
                chat_id,
            )
            await update.message.reply_text(
                "I received your payment receipt. 🧾\n"
                "Please send me the ticket number (for example: TKT-2026-03-00018) so I can register the payment:",
                do_quote=True,
            )
            return

        # Ticket in caption — run OCR now and complete payment
        typing_task = asyncio.create_task(
            _typing_loop(context.bot, update.effective_chat.id)
        )
        try:
            await _complete_payment(
                chat_id=chat_id,
                message=update.message,
                file_path=str(file_path),
                is_pdf=True,
                ticket_id=ticket_id,
            )
        finally:
            typing_task.cancel()

    except Exception as exc:
        logger.exception("Error processing PDF for telegram_id=%s: %s", chat_id, exc)
        await notify_error(
            exc,
            context=f"telegram_bot._handle_document | chat_id={chat_id}",
        )
        await update.message.reply_text(
            "An error occurred while registering the payment. Please try again.",
            do_quote=True,
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
                f"Perfect! Your number {phone} has been registered. How can I help you today? 🧀",
                do_quote=True,
            )
            return

        if chat_id not in _user_phones:
            # First interaction — ask for phone before anything else
            _pending_phone.add(chat_id)
            await update.message.reply_text(
                "Before we continue, I need your phone number "
                "(including country code, for example: +59899000000):",
                do_quote=True,
            )
            return

        if await _handle_pending_survey_response(
            chat_id=chat_id,
            incoming_msg=incoming_msg,
            message=update.message,
        ):
            return

        # ------------------------------------------------------------------
        # Pending payment receipt — waiting for ticket ID
        # ------------------------------------------------------------------
        if chat_id in _pending_receipt:
            match = _TICKET_ID_RE.search(incoming_msg)
            if match:
                ticket_id_pending = match.group().upper()
                logger.info(
                    "[receipt] Received ticket_id=%s for pending receipt of telegram_id=%s",
                    ticket_id_pending,
                    chat_id,
                )
                pending = _pending_receipt.pop(chat_id)
                typing_task = asyncio.create_task(
                    _typing_loop(context.bot, chat_id_int)
                )
                try:
                    await _complete_payment(
                        chat_id=chat_id,
                        message=update.message,
                        file_path=pending.file_path,
                        is_pdf=pending.is_pdf,
                        ticket_id=ticket_id_pending,
                    )
                finally:
                    typing_task.cancel()
                return

            # No ticket_id found — clear pending state and let message
            # fall through to the main AI agent (spec requirement).
            _pending_receipt.pop(chat_id, None)
            logger.info(
                "[receipt] No ticket_id in message for telegram_id=%s — "
                "clearing pending receipt and passing to AI agent",
                chat_id,
            )

        logger.info("=" * 80)
        logger.info("telegram_id=%s: %s", chat_id, incoming_msg)

        # ------------------------------------------------------------------
        # Human-control check: skip AI if operator has taken over this chat
        # ------------------------------------------------------------------
        if human_control.is_telegram_controlled(chat_id):
            logger.info(
                "[human-control] Skipping AI for telegram_id=%s — conversation under human control",
                chat_id,
            )
            return

        # Start typing indicator loop in background
        typing_task = asyncio.create_task(_typing_loop(context.bot, chat_id_int))

        try:
            await services.ensure_system_message(
                phone=chat_id,
                message=CHANNEL_MARKERS[CHANNEL_TELEGRAM],
            )
            await message_handler.save_user_msg(chat_id, incoming_msg)

            assert erp_client is not None, "ERP client not initialized"

            async def _send_photo_tg(image_bytes: bytes, caption: str) -> None:
                if update.message is None:
                    return
                await update.message.reply_photo(
                    photo=image_bytes, caption=caption, do_quote=True
                )

            deps = AgentDeps(
                erp_client=erp_client,
                db_services=services,
                whatsapp_client=_noop_whatsapp,
                user_phone=_user_phones.get(chat_id, ""),
                telegram_id=chat_id,
                send_photo_callback=_send_photo_tg,
            )

            agent = get_cheese_agent()
            history = await services.get_pydantic_ai_history(chat_id, hours=24)
            try:
                try:
                    result = await agent.run(
                        incoming_msg, deps=deps, message_history=history
                    )
                except ModelHTTPError as http_exc:
                    if http_exc.status_code == 503:
                        logger.warning(
                            "[fallback] 503 on primary model for telegram_id=%s — switching to %s",
                            chat_id,
                            FALLBACK_MODEL,
                        )
                        result = await agent.run(
                            incoming_msg,
                            deps=deps,
                            message_history=history,
                            model=FALLBACK_MODEL,
                        )
                    else:
                        raise
                ai_response: str = strip_markdown(result.output)
                tools_used = _extract_tools_used(result)
            except UsageLimitExceeded as ule:
                logger.warning(
                    "UsageLimitExceeded for telegram_id=%s: %s. Summarizing history and retrying...",
                    chat_id,
                    ule,
                )
                await notify_error(
                    ule,
                    context=f"_process_message | user={chat_id} | msg={incoming_msg[:200]} | action=summary_retry",
                )
                try:
                    chat_str = await services.get_chat_str(chat_id)
                    summary = await summarize_conversation(chat_str)
                    await services.reset_chat(chat_id)
                    await services.create_message(
                        phone=chat_id, role="system", message=summary
                    )
                    logger.info(
                        "[history] Summarized history and saved system message for %s",
                        chat_id,
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed to summarize history for %s: %s", chat_id, exc
                    )
                # Retry once with the compressed history
                try:
                    new_history = await services.get_pydantic_ai_history(
                        chat_id, hours=24
                    )
                    result = await agent.run(
                        incoming_msg, deps=deps, message_history=new_history
                    )
                    ai_response = strip_markdown(result.output)
                    tools_used = _extract_tools_used(result)
                except Exception as retry_exc:
                    logger.error(
                        "Retry after summary failed for %s: %s",
                        chat_id,
                        retry_exc,
                        exc_info=True,
                    )
                    await notify_error(
                        retry_exc,
                        context=f"_process_message | user={chat_id} | msg={incoming_msg[:200]} | action=retry_failed",
                    )
                    try:
                        explanation = await run_error_agent(str(retry_exc))
                        ai_response = explanation.user_message
                    except Exception as explainer_exc:
                        logger.error("Error agent also failed: %s", explainer_exc)
                        ai_response = (
                            "An error occurred while processing your message. "
                            "Please try again or type /restart."
                        )
                    tools_used = []
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
                        "An error occurred while processing your message. "
                        "Please try again or type /restart."
                    )
                tools_used = []

            logger.info("Agent response for telegram_id=%s: %s", chat_id, ai_response)
            logger.debug("Tools used: %s", tools_used)

            await message_handler.save_assistant_msg(chat_id, ai_response, tools_used)
            await update.message.reply_text(ai_response, do_quote=True)
            asyncio.create_task(_maybe_compress_history(chat_id, len(history)))
            assert erp_client is not None
            asyncio.create_task(
                upload_message_transcript(
                    client=erp_client,
                    phone_number=_user_phones.get(chat_id, chat_id),
                    user_message=incoming_msg,
                    bot_response=ai_response,
                )
            )

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
            "An error occurred while processing your message. "
            "Please try again or type /restart to restart the chat.",
            do_quote=True,
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
    telegram_commands.init_phones(_user_phones)
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
    app.add_handler(CommandHandler("cancelar", _handle_cancel))
    app.add_handler(CommandHandler("cancel", _handle_cancel))
    app.add_handler(CommandHandler("change_phone", _handle_change_phone))
    app.add_handler(CommandHandler("get_phone", cmd_get_phone))
    app.add_handler(
        CommandHandler("test_dev_notifications", cmd_test_dev_notifications)
    )

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

    # Booking commands — bypass AI agent
    app.add_handler(
        CommandHandler("get_reservation_status", cmd_get_reservation_status)
    )
    app.add_handler(CommandHandler("get_reservations", cmd_get_reservations))
    app.add_handler(
        CommandHandler("get_route_booking_status", cmd_get_route_booking_status)
    )
    app.add_handler(CommandHandler("get_itinerary", cmd_get_itinerary))
    app.add_handler(CommandHandler("cancel_reservation", cmd_cancel_reservation))
    app.add_handler(
        CommandHandler("list_available_experiences", cmd_list_available_experiences)
    )
    app.add_handler(CommandHandler("stop_followups", cmd_stop_followups))
    app.add_handler(CommandHandler("start_followups", cmd_start_followups))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, _handle_image))
    app.add_handler(MessageHandler(filters.Document.PDF, _handle_document))

    return app

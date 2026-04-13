from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from chatbot.ai_agent.models import ERP_BASE_PATH, PaymentInstructions
from chatbot.ai_agent.tools.erp_utils import extract_erp_data
from chatbot.ai_agent.translation_agent import localize_message_from_messages
from chatbot.db.services import Services
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.telegram_notifier import send_message as send_telegram_message
from chatbot.messaging.whatsapp import whatsapp_manager
from chatbot.reminders.lead_followup import (
    CHANNEL_TELEGRAM,
    FOLLOW_UP_OPTOUT_MARKER,
    infer_channel,
)

logger = logging.getLogger(__name__)

DEPOSIT_REMINDER_DELAY: timedelta = timedelta(hours=4)
CLIENT_INACTIVITY_THRESHOLD: timedelta = timedelta(hours=4)
SCAN_INTERVAL_SECONDS: int = 900  # 15 minutos
ERP_TIMEOUT_SECONDS: float = 15.0

_REMINDER_MESSAGE: str = (
    "⏰ *Reminder: pending deposit payment*\n\n"
    "Your reservation *{ticket_id}* was confirmed by the establishment, but the deposit payment has not been registered yet.\n\n"
    "💳 Deposit payment instructions\n"
    "Ticket: {ticket_id}\n"
    "Required amount: {amount_required} UYU\n"
    "Amount paid: {amount_paid} UYU\n"
    "Amount remaining: {amount_remaining} UYU"
    "{instructions_block}\n\n"
    "📎 Send the payment receipt with the number {ticket_id} as the image or document caption."
)


async def _get_payment_instructions(
    erp_client: httpx.AsyncClient,
    ticket_id: str,
) -> PaymentInstructions | None:
    """Consulta el ERP para obtener las instrucciones de pago del depósito.

    Args:
        erp_client: Cliente HTTP del ERP.
        ticket_id: Identificador del ticket.

    Returns:
        PaymentInstructions si la consulta fue exitosa, None en caso de error.
    """
    try:
        response = await erp_client.post(
            f"{ERP_BASE_PATH}.deposit_controller.get_deposit_instructions",
            json={"ticket_id": ticket_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data: Any = extract_erp_data(response.json())
        return PaymentInstructions.model_validate(data)
    except Exception as exc:
        logger.error(
            "[deposit_reminder] Error al obtener instrucciones de pago ticket=%s: %s",
            ticket_id,
            exc,
        )
        return None


def _build_reminder_message(pay_info: PaymentInstructions) -> str:
    """Construye el mensaje de recordatorio con los detalles del pago pendiente."""
    instructions_block = f"\n\n{pay_info.instructions}" if pay_info.instructions else ""
    return _REMINDER_MESSAGE.format(
        ticket_id=pay_info.ticket_id,
        amount_required=pay_info.amount_required
        if pay_info.amount_required is not None
        else 0,
        amount_paid=pay_info.amount_paid if pay_info.amount_paid is not None else 0,
        amount_remaining=pay_info.amount_remaining
        if pay_info.amount_remaining is not None
        else 0,
        instructions_block=instructions_block,
    )


async def process_pending_deposit_reminders(
    db_services: Services,
    erp_client: httpx.AsyncClient,
) -> None:
    """Procesa los tickets confirmados cuya seña no fue pagada.

    Envía hasta 3 recordatorios por ticket con al menos 4h de separación entre ellos,
    respetando el opt-out del cliente y verificando que el cliente lleve más de 4h sin escribir.
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now - DEPOSIT_REMINDER_DELAY

    pending = await db_services.get_pending_deposit_reminders(cutoff)
    if not pending:
        logger.debug("[deposit_reminder] No hay tickets pendientes de recordatorio")
        return

    logger.info(
        "[deposit_reminder] %d ticket(s) candidatos a recordatorio", len(pending)
    )

    for row in pending:
        ticket_id: str = row.ticket_id  # type: ignore[attr-defined]
        phone: str = row.phone  # type: ignore[attr-defined]

        try:
            messages = await db_services.get_messages(phone)

            # Verificar opt-out del cliente
            opted_out = any(
                getattr(m, "role", None) == "system"
                and getattr(m, "message", "") == FOLLOW_UP_OPTOUT_MARKER
                for m in messages
            )
            if opted_out:
                logger.debug(
                    "[deposit_reminder] %s opt-out activo, omitiendo ticket=%s",
                    phone,
                    ticket_id,
                )
                continue

            # Verificar inactividad mínima de 4h del cliente
            last_user_msg = await db_services.get_last_user_message(phone)
            if last_user_msg is not None:
                last_user_at = getattr(last_user_msg, "created_at", None)
                if last_user_at is not None:
                    if last_user_at.tzinfo is not None:
                        last_user_at = last_user_at.replace(tzinfo=None)
                    if now - last_user_at < CLIENT_INACTIVITY_THRESHOLD:
                        logger.debug(
                            "[deposit_reminder] %s estuvo activo recientemente, omitiendo ticket=%s",
                            phone,
                            ticket_id,
                        )
                        continue
            pay_info = await _get_payment_instructions(
                erp_client=erp_client, ticket_id=ticket_id
            )
            if pay_info is None:
                # Error al consultar el ERP — no marcar como procesado para reintentar
                continue

            if not pay_info.amount_remaining or pay_info.amount_remaining <= 0:
                logger.info(
                    "[deposit_reminder] ticket=%s ya pagado (amount_remaining=%s), excluyendo permanentemente",
                    ticket_id,
                    pay_info.amount_remaining,
                )
                await db_services.mark_deposit_paid(ticket_id)
                continue

            message = _build_reminder_message(pay_info)
            localized_message = await localize_message_from_messages(messages, message)
            channel = infer_channel(conversation_id=phone, messages=messages)
            if channel == CHANNEL_TELEGRAM:
                ok = await send_telegram_message(chat_id=phone, text=localized_message)
            else:
                ok = await whatsapp_manager.send_text(
                    user_number=phone, text=localized_message
                )
            if not ok:
                logger.error(
                    "[deposit_reminder] Error enviando recordatorio a %s via %s (ticket=%s)",
                    phone,
                    channel,
                    ticket_id,
                )
                continue

            await db_services.mark_deposit_reminder_sent(ticket_id)
            logger.info(
                "[deposit_reminder] Recordatorio enviado a %s via %s (ticket=%s amount_remaining=%s)",
                phone,
                channel,
                ticket_id,
                pay_info.amount_remaining,
            )
        except Exception as exc:
            logger.exception(
                "[deposit_reminder] Error procesando ticket=%s phone=%s: %s",
                ticket_id,
                phone,
                exc,
            )
            await notify_error(
                exc,
                context=f"deposit_reminder | ticket_id={ticket_id} | phone={phone}",
            )


async def deposit_reminder_worker(
    db_services: Services,
    erp_client: httpx.AsyncClient,
) -> None:
    """Worker que escanea periódicamente los tickets confirmados pendientes de pago de seña."""
    logger.info("[deposit_reminder] Worker iniciado")
    while True:
        try:
            await process_pending_deposit_reminders(
                db_services=db_services,
                erp_client=erp_client,
            )
        except Exception as exc:
            logger.exception("[deposit_reminder] Ciclo del worker fallido: %s", exc)
            await notify_error(exc, context="deposit_reminder_worker")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)

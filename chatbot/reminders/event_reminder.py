from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from typing import Any

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

EVENT_REMINDER_WINDOW: timedelta = timedelta(hours=8)
SCAN_INTERVAL_SECONDS: int = 900  # 15 minutos

_EVENT_REMINDER_MESSAGE: str = (
    "⏰ *Reminder: your event is today!*\n\n"
    "Your reservation *{ticket_id}* is scheduled for today at *{slot_time}*.\n\n"
    "We look forward to seeing you! 🧀"
)


def _parse_slot_time(raw: str) -> time | None:
    """Parsea el horario del slot desde el formato del ERP (ej: '9:00:00')."""
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _is_within_reminder_window(slot_time: time, now: datetime) -> bool:
    """Verifica si el horario del evento está dentro de las próximas 8h."""
    today = now.date()
    event_dt: datetime = datetime.combine(today, slot_time, tzinfo=UTC)
    diff: timedelta = event_dt - now
    return timedelta(0) <= diff <= EVENT_REMINDER_WINDOW


async def process_pending_event_reminders(db_services: Services) -> None:
    """Procesa tickets confirmados cuyo evento es hoy y envía recordatorio."""
    now: datetime = datetime.now(UTC)
    today_start: datetime = datetime.combine(now.date(), time.min).replace(tzinfo=None)
    today_end: datetime = datetime.combine(now.date(), time.max).replace(tzinfo=None)

    pending: list[Any] = await db_services.get_pending_event_reminders(
        today_start=today_start, today_end=today_end
    )
    if not pending:
        logger.debug(
            "[event_reminder] No hay tickets pendientes de recordatorio de evento"
        )
        return

    logger.info(
        "[event_reminder] %d ticket(s) candidatos a recordatorio de evento",
        len(pending),
    )

    for row in pending:
        ticket_id: str = row.ticket_id  # type: ignore[attr-defined]
        phone: str = row.phone  # type: ignore[attr-defined]
        raw_slot_time: str | None = row.slot_time  # type: ignore[attr-defined]

        if not raw_slot_time:
            continue

        parsed_time: time | None = _parse_slot_time(raw_slot_time)
        if parsed_time is None:
            logger.warning(
                "[event_reminder] slot_time inválido para ticket=%s: %s",
                ticket_id,
                raw_slot_time,
            )
            continue

        if not _is_within_reminder_window(parsed_time, now):
            logger.debug(
                "[event_reminder] ticket=%s slot_time=%s fuera de ventana de 8h",
                ticket_id,
                raw_slot_time,
            )
            continue

        try:
            messages: list[Any] = await db_services.get_messages(phone)

            # Verificar opt-out del cliente
            opted_out: bool = any(
                getattr(m, "role", None) == "system"
                and getattr(m, "message", "") == FOLLOW_UP_OPTOUT_MARKER
                for m in messages
            )
            if opted_out:
                logger.debug(
                    "[event_reminder] %s opt-out activo, omitiendo ticket=%s",
                    phone,
                    ticket_id,
                )
                await db_services.mark_event_notified(ticket_id)
                continue

            # Formatear hora para el mensaje (HH:MM)
            display_time: str = parsed_time.strftime("%H:%M")
            message: str = _EVENT_REMINDER_MESSAGE.format(
                ticket_id=ticket_id,
                slot_time=display_time,
            )

            channel: str = infer_channel(conversation_id=phone, messages=messages)
            if channel == CHANNEL_TELEGRAM:
                ok: bool = await send_telegram_message(chat_id=phone, text=message)
            else:
                ok = await whatsapp_manager.send_text(user_number=phone, text=message)

            if not ok:
                logger.error(
                    "[event_reminder] Error enviando recordatorio a %s via %s (ticket=%s)",
                    phone,
                    channel,
                    ticket_id,
                )
                continue

            await db_services.mark_event_notified(ticket_id)
            await db_services.create_message(
                phone=phone,
                role="assistant",
                message=f"Bot - {message}",
            )
            logger.info(
                "[event_reminder] Recordatorio enviado a %s via %s (ticket=%s slot_time=%s)",
                phone,
                channel,
                ticket_id,
                display_time,
            )
        except Exception as exc:
            logger.exception(
                "[event_reminder] Error procesando ticket=%s phone=%s: %s",
                ticket_id,
                phone,
                exc,
            )
            await notify_error(
                exc,
                context=f"event_reminder | ticket_id={ticket_id} | phone={phone}",
            )


async def event_reminder_worker(db_services: Services) -> None:
    """Worker que escanea periódicamente tickets confirmados con evento hoy."""
    logger.info("[event_reminder] Worker iniciado")
    while True:
        try:
            await process_pending_event_reminders(db_services=db_services)
        except Exception as exc:
            logger.exception("[event_reminder] Ciclo del worker fallido: %s", exc)
            await notify_error(exc, context="event_reminder_worker")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)

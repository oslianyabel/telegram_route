import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from chatbot.api.utils.models import (
    Messages,
    ReminderItem,
    ReminderStatus,
    ReminderType,
    User,
)
from chatbot.api.utils.security import get_api_key
from chatbot.db.services import services
from chatbot.reminders.utils import parse_slot_time

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_api_key)])

_DEPOSIT_REMINDER_DELAY: timedelta = timedelta(hours=4)
_LEAD_FOLLOWUP_DELAY: timedelta = timedelta(hours=4)


@router.get("/users", response_model=list[User])
async def get_all_users():
    logger.info("Fetching all users")
    return await services.get_all_users()


@router.get("/users/{phone}", response_model=User)
async def get_user(phone: str):
    logger.info(f"Fetching user with phone: {phone}")
    return await services.get_user(phone)


@router.get("/messages/{phone}", response_model=list[Messages])
async def get_messages(phone: str):
    logger.info(f"Fetching messages for phone: {phone}")
    return await services.get_messages(phone)


@router.get("/reminders", response_model=list[ReminderItem])
async def get_reminders(
    reminder_type: ReminderType | None = None,
    status: ReminderStatus | None = None,
) -> list[ReminderItem]:
    logger.info("Fetching reminders type=%s status=%s", reminder_type, status)
    results: list[ReminderItem] = []
    status_values: list[str] = (
        [s.value for s in ReminderStatus] if status is None else [status.value]
    )
    type_values: list[str] = (
        [t.value for t in ReminderType]
        if reminder_type is None
        else [reminder_type.value]
    )

    for s in status_values:
        if "deposit" in type_values:
            rows = await services.get_deposit_reminders_by_status(s)
            for row in rows:
                reminded_at: datetime | None = row.reminded_at  # type: ignore[attr-defined]
                confirmed_at: datetime | None = row.confirmed_at  # type: ignore[attr-defined]
                if s == "pending":
                    base_dt = reminded_at if reminded_at is not None else confirmed_at
                    scheduled_at = (
                        base_dt + _DEPOSIT_REMINDER_DELAY if base_dt else None
                    )
                else:
                    scheduled_at = reminded_at
                results.append(
                    ReminderItem(
                        phone=row.phone,  # type: ignore[attr-defined]
                        name=row.name,  # type: ignore[attr-defined]
                        reminder_type=ReminderType.deposit,
                        status=ReminderStatus(s),
                        scheduled_at=scheduled_at,
                        ticket_id=row.ticket_id,  # type: ignore[attr-defined]
                    )
                )

        if "event" in type_values:
            rows = await services.get_event_reminders_by_status(s)
            for row in rows:
                ticket_date: datetime | None = row.ticket_date  # type: ignore[attr-defined]
                raw_slot: str | None = row.slot_time  # type: ignore[attr-defined]
                if ticket_date and raw_slot:
                    parsed = parse_slot_time(raw_slot)
                    scheduled_at = (
                        datetime.combine(ticket_date.date(), parsed)
                        if parsed
                        else ticket_date
                    )
                else:
                    scheduled_at = ticket_date
                results.append(
                    ReminderItem(
                        phone=row.phone,  # type: ignore[attr-defined]
                        name=row.name,  # type: ignore[attr-defined]
                        reminder_type=ReminderType.event,
                        status=ReminderStatus(s),
                        scheduled_at=scheduled_at,
                        ticket_id=row.ticket_id,  # type: ignore[attr-defined]
                    )
                )

        if "lead_followup" in type_values:
            rows = await services.get_lead_followup_reminders_by_status(s)
            for row in rows:
                if s == "pending":
                    last_user_at: datetime | None = row.last_user_at  # type: ignore[attr-defined]
                    scheduled_at = (
                        last_user_at + _LEAD_FOLLOWUP_DELAY if last_user_at else None
                    )
                else:
                    scheduled_at = row.scheduled_at  # type: ignore[attr-defined]
                results.append(
                    ReminderItem(
                        phone=row.phone,  # type: ignore[attr-defined]
                        name=row.name,  # type: ignore[attr-defined]
                        reminder_type=ReminderType.lead_followup,
                        status=ReminderStatus(s),
                        scheduled_at=scheduled_at,
                        ticket_id=None,
                    )
                )

    return results

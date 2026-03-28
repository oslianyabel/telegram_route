from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from chatbot.ai_agent.lead_followup_agent import generate_lead_followup_message
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    AvailabilityResponse,
    Route,
    RouteAvailabilityResponse,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data
from chatbot.core import human_control
from chatbot.db.services import Services
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.telegram_notifier import send_message as send_telegram_message
from chatbot.messaging.whatsapp import whatsapp_manager

logger = logging.getLogger(__name__)

LEAD_TOOL = "upsert_lead"
RESERVATION_TOOLS = {"create_pending_reservation", "create_route_reservation"}
FOLLOW_UP_TOOL = "lead_followup_reminder"
FOLLOW_UP_OPTOUT_MARKER = "FOLLOWUP: opt_out"
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_TELEGRAM = "telegram"
CHANNEL_MARKERS = {
    CHANNEL_WHATSAPP: "CHANNEL: whatsapp",
    CHANNEL_TELEGRAM: "CHANNEL: telegram",
}
INITIAL_FOLLOW_UP_DELAY = timedelta(hours=2)
REPEAT_FOLLOW_UP_DELAY = timedelta(hours=20)
MAX_INACTIVE_WINDOW = timedelta(hours=24)
WHATSAPP_FREE_WINDOW = timedelta(hours=24)
SCAN_INTERVAL_SECONDS = 1800
ERP_TIMEOUT_SECONDS = 15.0
MAX_ROUTES_TO_CHECK = 10
MAX_FOLLOW_UPS_PER_CONVERSATION = 3

_PARTY_SIZE_PATTERNS = [
    re.compile(r"\bsomos\s+(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\bpara\s+(\d{1,2})\s+personas?\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s+personas?\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s+adultos?\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class FollowUpDecision:
    should_send: bool
    reason: str
    last_user_at: datetime | None = None
    last_reminder_at: datetime | None = None
    followup_count: int = 0


def _parse_tools(raw_tools: Any) -> list[str]:
    if raw_tools is None:
        return []
    if isinstance(raw_tools, list):
        return [str(item) for item in raw_tools]
    if isinstance(raw_tools, str):
        try:
            data = json.loads(raw_tools)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [str(item) for item in data]
    return []


def _normalize_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def evaluate_follow_up_eligibility(
    messages: list[Any],
    now: datetime,
) -> FollowUpDecision:
    lead_detected = False
    reservation_detected = False
    last_user_at: datetime | None = None
    last_reminder_at: datetime | None = None
    followup_count = 0
    opted_out = False

    for row in messages:
        role = getattr(row, "role", None)
        created_at = _normalize_dt(getattr(row, "created_at", None))
        tools = _parse_tools(getattr(row, "tools_used", None))
        message = getattr(row, "message", "")

        if LEAD_TOOL in tools:
            lead_detected = True
        if RESERVATION_TOOLS.intersection(tools):
            reservation_detected = True
        if FOLLOW_UP_TOOL in tools and created_at is not None:
            followup_count += 1
            if last_reminder_at is None or created_at > last_reminder_at:
                last_reminder_at = created_at
        if role == "system" and message == FOLLOW_UP_OPTOUT_MARKER:
            opted_out = True
        if role == "user" and created_at is not None:
            if last_user_at is None or created_at > last_user_at:
                last_user_at = created_at

    if not lead_detected:
        return FollowUpDecision(
            False,
            "lead_not_detected",
            last_user_at,
            last_reminder_at,
            followup_count,
        )
    if reservation_detected:
        return FollowUpDecision(
            False,
            "reservation_already_created",
            last_user_at,
            last_reminder_at,
            followup_count,
        )
    if last_user_at is None:
        return FollowUpDecision(
            False,
            "no_user_messages",
            last_user_at,
            last_reminder_at,
            followup_count,
        )
    if opted_out:
        return FollowUpDecision(
            False,
            "followup_opted_out",
            last_user_at,
            last_reminder_at,
            followup_count,
        )
    if followup_count >= MAX_FOLLOW_UPS_PER_CONVERSATION:
        return FollowUpDecision(
            False,
            "followup_limit_reached",
            last_user_at,
            last_reminder_at,
            followup_count,
        )

    inactive_for = now - last_user_at
    if inactive_for > MAX_INACTIVE_WINDOW:
        return FollowUpDecision(
            False,
            "inactive_window_expired",
            last_user_at,
            last_reminder_at,
            followup_count,
        )
    if last_reminder_at is None or last_user_at > last_reminder_at:
        if inactive_for >= INITIAL_FOLLOW_UP_DELAY:
            return FollowUpDecision(
                True,
                "initial_delay_elapsed",
                last_user_at,
                last_reminder_at,
                followup_count,
            )
        return FollowUpDecision(
            False,
            "initial_delay_not_elapsed",
            last_user_at,
            last_reminder_at,
            followup_count,
        )

    if now - last_reminder_at >= REPEAT_FOLLOW_UP_DELAY:
        return FollowUpDecision(
            True,
            "repeat_delay_elapsed",
            last_user_at,
            last_reminder_at,
            followup_count,
        )

    return FollowUpDecision(
        False,
        "repeat_delay_not_elapsed",
        last_user_at,
        last_reminder_at,
        followup_count,
    )


def infer_channel(conversation_id: str, messages: list[Any]) -> str:
    for row in messages:
        if getattr(row, "role", None) != "system":
            continue
        message = getattr(row, "message", "")
        if message == CHANNEL_MARKERS[CHANNEL_TELEGRAM]:
            return CHANNEL_TELEGRAM
        if message == CHANNEL_MARKERS[CHANNEL_WHATSAPP]:
            return CHANNEL_WHATSAPP

    if conversation_id.startswith("-"):
        return CHANNEL_TELEGRAM
    return CHANNEL_WHATSAPP


def infer_party_size(messages: list[Any]) -> int | None:
    for row in reversed(messages):
        text = getattr(row, "message", "")
        if not text:
            continue
        text = text.removeprefix("Usuario - ").removeprefix("Bot - ")
        for pattern in _PARTY_SIZE_PATTERNS:
            match = pattern.search(text)
            if match:
                party_size = int(match.group(1))
                if 1 <= party_size <= 20:
                    return party_size
    return None


def format_conversation_history(messages: list[Any]) -> str:
    lines: list[str] = []
    for row in messages:
        created_at = _normalize_dt(getattr(row, "created_at", None))
        created_at_str = created_at.isoformat() if created_at else "unknown"
        role = getattr(row, "role", "unknown")
        message = getattr(row, "message", "")
        tools = _parse_tools(getattr(row, "tools_used", None))
        tools_str = f" | tools={', '.join(tools)}" if tools else ""
        lines.append(f"[{created_at_str}] {role}: {message}{tools_str}")
    return "\n".join(lines)


def _serialize_experience_availability(
    items: list[AvailabilityResponse],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in items:
        available_slots = [
            {
                "slot_id": slot.slot_id,
                "date": slot.date,
                "time": slot.time,
                "available_capacity": slot.available_capacity,
            }
            for slot in item.slots
            if slot.is_available
        ]
        if not available_slots:
            continue
        serialized.append(
            {
                "experience_id": item.experience_id,
                "experience_name": item.experience_name,
                "date": item.date,
                "available_slots_count": len(available_slots),
                "slots": available_slots[:5],
            }
        )
    return serialized


def _serialize_route_availability(
    items: list[RouteAvailabilityResponse],
) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in items:
        if not item.available:
            continue
        experiences = []
        for exp in item.experiences:
            experiences.append(
                {
                    "experience_id": exp.experience_id,
                    "experience_name": exp.experience_name,
                    "available": exp.available,
                    "available_slots_count": exp.available_slots_count,
                }
            )
        serialized.append(
            {
                "route_id": item.route_id,
                "date": item.date,
                "party_size": item.party_size,
                "available": item.available,
                "experiences": experiences,
            }
        )
    return serialized


async def _fetch_experience_availability(
    erp_client: httpx.AsyncClient,
    start_date: date,
    end_date: date,
) -> list[AvailabilityResponse]:
    response = await erp_client.post(
        f"{ERP_BASE_PATH}.availability_controller.get_availability",
        json={
            "date_from": start_date.strftime("%d-%m-%Y"),
            "date_to": end_date.strftime("%d-%m-%Y"),
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = extract_erp_data(response.json())
    if isinstance(data, dict):
        data = [data]
    return [AvailabilityResponse.model_validate(item) for item in data]


async def _fetch_route_catalog(erp_client: httpx.AsyncClient) -> list[Route]:
    response = await erp_client.post(
        f"{ERP_BASE_PATH}.route_controller.list_routes",
        json={"page": 1, "page_size": MAX_ROUTES_TO_CHECK, "status": "ONLINE"},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = extract_erp_data(response.json())
    return [Route.model_validate(item) for item in data]


async def _fetch_route_availability(
    erp_client: httpx.AsyncClient,
    routes: list[Route],
    party_size: int,
    start_date: date,
) -> list[RouteAvailabilityResponse]:
    results: list[RouteAvailabilityResponse] = []
    for route in routes:
        for offset in range(7):
            candidate = start_date + timedelta(days=offset)
            response = await erp_client.post(
                f"{ERP_BASE_PATH}.availability_controller.get_route_availability",
                json={
                    "route_id": route.route_id,
                    "date": candidate.isoformat(),
                    "party_size": party_size,
                },
                timeout=ERP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = extract_erp_data(response.json())
            results.append(RouteAvailabilityResponse.model_validate(data))
    return results


async def build_follow_up_payload(
    conversation_id: str,
    messages: list[Any],
    erp_client: httpx.AsyncClient,
) -> dict[str, Any]:
    start_date = datetime.now(UTC).date()
    end_date = start_date + timedelta(days=6)
    experience_availability = await _fetch_experience_availability(
        erp_client=erp_client,
        start_date=start_date,
        end_date=end_date,
    )
    routes = await _fetch_route_catalog(erp_client)
    party_size = infer_party_size(messages) or 1

    route_availability = await _fetch_route_availability(
        erp_client=erp_client,
        routes=routes,
        party_size=party_size,
        start_date=start_date,
    )
    route_section: dict[str, Any] = {
        "party_size_inferred": party_size,
        "availability": _serialize_route_availability(route_availability),
        "catalog": [
            {
                "route_id": route.route_id,
                "name": route.name,
                "description": route.description,
                "experiences_count": route.experiences_count,
            }
            for route in routes
        ],
    }

    return {
        "conversation_id": conversation_id,
        "history": format_conversation_history(messages),
        "availability_window": {
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
        },
        "experience_availability": _serialize_experience_availability(
            experience_availability
        ),
        "routes": route_section,
    }


async def _send_follow_up_message(
    conversation_id: str,
    channel: str,
    message: str,
) -> bool:
    if channel == CHANNEL_TELEGRAM:
        if human_control.is_telegram_controlled(conversation_id):
            logger.info(
                "[lead_followup] Skipping Telegram follow-up for %s under human control",
                conversation_id,
            )
            return False
        return await send_telegram_message(chat_id=conversation_id, text=message)

    if human_control.is_whatsapp_controlled(conversation_id):
        logger.info(
            "[lead_followup] Skipping WhatsApp follow-up for %s under human control",
            conversation_id,
        )
        return False
    return await whatsapp_manager.send_text(user_number=conversation_id, text=message)


def _whatsapp_window_open(last_user_at: datetime | None, now: datetime) -> bool:
    if last_user_at is None:
        return False
    return now - last_user_at <= WHATSAPP_FREE_WINDOW


async def process_pending_lead_followups(
    db_services: Services,
    erp_client: httpx.AsyncClient,
) -> None:
    users = await db_services.get_all_users()
    now = datetime.now(UTC)

    for user in users:
        conversation_id = str(user.phone)  # type: ignore[attr-defined]
        try:
            messages = await db_services.get_messages(conversation_id)
            decision = evaluate_follow_up_eligibility(messages, now=now)
            if not decision.should_send:
                logger.debug(
                    "[lead_followup] %s skipped (%s)",
                    conversation_id,
                    decision.reason,
                )
                continue

            channel = infer_channel(conversation_id, messages)
            if channel == CHANNEL_WHATSAPP and not _whatsapp_window_open(
                decision.last_user_at,
                now,
            ):
                logger.info(
                    "[lead_followup] %s skipped (whatsapp_window_closed)",
                    conversation_id,
                )
                continue
            payload = await build_follow_up_payload(
                conversation_id=conversation_id,
                messages=messages,
                erp_client=erp_client,
            )
            followup_message = await generate_lead_followup_message(payload)
            ok = await _send_follow_up_message(
                conversation_id=conversation_id,
                channel=channel,
                message=followup_message,
            )
            if not ok:
                logger.error(
                    "[lead_followup] Failed sending follow-up to %s via %s",
                    conversation_id,
                    channel,
                )
                continue

            await db_services.create_message(
                phone=conversation_id,
                role="assistant",
                message=f"Bot - {followup_message}",
                tools_used=[FOLLOW_UP_TOOL],
            )
            logger.info(
                "[lead_followup] Follow-up sent to %s via %s",
                conversation_id,
                channel,
            )
        except Exception as exc:
            logger.exception(
                "[lead_followup] Error processing conversation %s: %s",
                conversation_id,
                exc,
            )
            await notify_error(
                exc,
                context=f"lead_followup | conversation_id={conversation_id}",
            )


async def lead_followup_worker(
    db_services: Services,
    erp_client: httpx.AsyncClient,
) -> None:
    logger.info("[lead_followup] Worker started")
    while True:
        try:
            await process_pending_lead_followups(
                db_services=db_services,
                erp_client=erp_client,
            )
        except Exception as exc:
            logger.exception("[lead_followup] Worker cycle failed: %s", exc)
            await notify_error(exc, context="lead_followup_worker")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)

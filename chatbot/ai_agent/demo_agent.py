"""Demo agent for the marketing website.

A slim version of the main cheese agent that:
- Includes catalog, availability, date resolution tools.
- Replaces ERP-backed reservation tools with in-memory mocks.
- Excludes CRM, payments, reminders, and post-sale support tools.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.settings import ModelSettings

from chatbot.ai_agent.agent import _load_system_prompt, _once_per_turn
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import GoogleModel
from chatbot.ai_agent.tools.catalog import (
    get_availability,
    get_establishment_details,
    get_experience_detail,
    get_route_availability,
    get_route_detail,
    list_establishments,
    list_experiences,
    list_experiences_by_availability,
    list_routes,
)
from chatbot.ai_agent.tools.date_resolver import resolve_relative_date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory store — keyed by session_id (= user_phone in AgentDeps)
# ---------------------------------------------------------------------------


@dataclass
class DemoTicket:
    ticket_id: str
    experience_id: str
    slot_id: str
    party_size: int
    selected_date: str
    status: str = "PENDING"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class DemoRouteBooking:
    route_booking_id: str
    route_id: str
    party_size: int
    date_from: str
    date_to: str
    status: str = "PENDING"
    tickets: list[DemoTicket] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class DemoSessionData:
    tickets: dict[str, DemoTicket] = field(default_factory=dict)
    route_bookings: dict[str, DemoRouteBooking] = field(default_factory=dict)
    ticket_counter: int = 0
    route_counter: int = 0


_demo_store: dict[str, DemoSessionData] = {}


def _get_session(session_id: str) -> DemoSessionData:
    if session_id not in _demo_store:
        _demo_store[session_id] = DemoSessionData()
    return _demo_store[session_id]


def _session_prefix(session_id: str) -> str:
    return session_id[:4].upper()


# ---------------------------------------------------------------------------
# In-memory reservation tools
# ---------------------------------------------------------------------------


async def create_pending_reservation(
    ctx: RunContext[AgentDeps],
    experience_id: str,
    slot_id: str,
    party_size: int,
    selected_date: str,
) -> str:
    """Create a PENDING demo reservation for an experience slot.

    Saves the reservation in the in-memory demo store for this session.
    Inform the user their reservation is pending and that they would need to
    complete payment to confirm it in a real booking. This is a demo — no real
    reservation is created in the system.

    Args:
        ctx: Agent run context with dependencies.
        experience_id: ID of the experience to book.
        slot_id: ID of the slot to reserve.
        party_size: Number of people in the group.
        selected_date: Reservation date in YYYY-MM-DD format.
    """
    logger.info(
        "[demo] create_pending_reservation session=%s experience_id=%s slot_id=%s party_size=%s date=%s",
        ctx.deps.user_phone,
        experience_id,
        slot_id,
        party_size,
        selected_date,
    )
    session = _get_session(ctx.deps.user_phone)
    session.ticket_counter += 1
    prefix = _session_prefix(ctx.deps.user_phone)
    ticket_id = f"DEMO-TKT-{prefix}-{session.ticket_counter:04d}"

    ticket = DemoTicket(
        ticket_id=ticket_id,
        experience_id=experience_id,
        slot_id=slot_id,
        party_size=party_size,
        selected_date=selected_date,
    )
    session.tickets[ticket_id] = ticket

    return (
        f"Demo reservation created successfully!\n"
        f"- Ticket ID: {ticket_id}\n"
        f"- Experience: {experience_id}\n"
        f"- Slot: {slot_id}\n"
        f"- Party size: {party_size}\n"
        f"- Date: {selected_date}\n"
        f"- Status: PENDING\n"
        f"(This is a demo — no real reservation has been made in the system.)"
    )


async def create_route_reservation(
    ctx: RunContext[AgentDeps],
    route_id: str,
    date_from: str,
    date_to: str,
    party_size: int,
) -> str:
    """Create a PENDING demo route booking.

    Saves the route booking in the in-memory demo store for this session.
    Inform the user this is a demo booking. No real reservation is created.

    Args:
        ctx: Agent run context with dependencies.
        route_id: ID of the route to book.
        date_from: Start date of the route (YYYY-MM-DD).
        date_to: End date of the route (YYYY-MM-DD).
        party_size: Number of people.
    """
    logger.info(
        "[demo] create_route_reservation session=%s route_id=%s date_from=%s date_to=%s party_size=%s",
        ctx.deps.user_phone,
        route_id,
        date_from,
        date_to,
        party_size,
    )
    session = _get_session(ctx.deps.user_phone)
    session.route_counter += 1
    prefix = _session_prefix(ctx.deps.user_phone)
    route_booking_id = f"DEMO-RB-{prefix}-{session.route_counter:04d}"

    # Create a single representative ticket for the route
    session.ticket_counter += 1
    ticket_id = f"DEMO-TKT-{prefix}-{session.ticket_counter:04d}"
    ticket = DemoTicket(
        ticket_id=ticket_id,
        experience_id=route_id,
        slot_id="route-slot",
        party_size=party_size,
        selected_date=date_from,
    )

    booking = DemoRouteBooking(
        route_booking_id=route_booking_id,
        route_id=route_id,
        party_size=party_size,
        date_from=date_from,
        date_to=date_to,
        tickets=[ticket],
    )
    session.route_bookings[route_booking_id] = booking
    session.tickets[ticket_id] = ticket

    return (
        f"Demo route booking created successfully!\n"
        f"- Route Booking ID: {route_booking_id}\n"
        f"- Route: {route_id}\n"
        f"- Party size: {party_size}\n"
        f"- From: {date_from} To: {date_to}\n"
        f"- Status: PENDING\n"
        f"- Ticket ID: {ticket_id}\n"
        f"(This is a demo — no real reservation has been made in the system.)"
    )


async def get_reservation_status(
    ctx: RunContext[AgentDeps],
    reservation_id: str,
) -> str:
    """Get details and status of a demo reservation by its ID.

    Args:
        ctx: Agent run context with dependencies.
        reservation_id: Demo ticket ID (e.g. 'DEMO-TKT-A3F2-0001').
    """
    logger.info(
        "[demo] get_reservation_status session=%s reservation_id=%s",
        ctx.deps.user_phone,
        reservation_id,
    )
    session = _get_session(ctx.deps.user_phone)
    ticket = session.tickets.get(reservation_id)
    if ticket is None:
        return f"No demo reservation found with ID '{reservation_id}'."

    return (
        f"Reservation details:\n"
        f"- Ticket ID: {ticket.ticket_id}\n"
        f"- Experience: {ticket.experience_id}\n"
        f"- Slot: {ticket.slot_id}\n"
        f"- Party size: {ticket.party_size}\n"
        f"- Date: {ticket.selected_date}\n"
        f"- Status: {ticket.status}\n"
        f"- Created at: {ticket.created_at}"
    )


async def get_route_booking_status(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
) -> str:
    """Get details and status of a demo route booking by its ID.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: Demo route booking ID (e.g. 'DEMO-RB-A3F2-0001').
    """
    logger.info(
        "[demo] get_route_booking_status session=%s route_booking_id=%s",
        ctx.deps.user_phone,
        route_booking_id,
    )
    session = _get_session(ctx.deps.user_phone)
    booking = session.route_bookings.get(route_booking_id)
    if booking is None:
        return f"No demo route booking found with ID '{route_booking_id}'."

    tickets_str = "\n".join(
        f"  - {t.ticket_id}: {t.experience_id} on {t.selected_date} ({t.status})"
        for t in booking.tickets
    )
    return (
        f"Route booking details:\n"
        f"- Route Booking ID: {booking.route_booking_id}\n"
        f"- Route: {booking.route_id}\n"
        f"- Party size: {booking.party_size}\n"
        f"- From: {booking.date_from} To: {booking.date_to}\n"
        f"- Status: {booking.status}\n"
        f"- Tickets:\n{tickets_str}\n"
        f"- Created at: {booking.created_at}"
    )


async def get_reservations_by_phone(
    ctx: RunContext[AgentDeps],
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """List all demo reservations for this session.

    Args:
        ctx: Agent run context with dependencies.
        status: Optional filter by status (PENDING, CANCELLED).
        page: Page number (1-based).
        page_size: Number of results per page.
    """
    logger.info(
        "[demo] get_reservations_by_phone session=%s status=%s page=%s",
        ctx.deps.user_phone,
        status,
        page,
    )
    session = _get_session(ctx.deps.user_phone)
    all_tickets = list(session.tickets.values())

    if status:
        all_tickets = [t for t in all_tickets if t.status == status.upper()]

    total = len(all_tickets)
    start = (page - 1) * page_size
    end = start + page_size
    page_tickets = all_tickets[start:end]

    if not page_tickets:
        return "No demo reservations found for this session."

    lines = [f"Demo reservations (total: {total}):"]
    for t in page_tickets:
        lines.append(
            f"- {t.ticket_id}: {t.experience_id} on {t.selected_date} — {t.status}"
        )
    return "\n".join(lines)


async def cancel_reservation(
    ctx: RunContext[AgentDeps],
    reservation_id: str,
    confirmed: bool = False,
) -> str:
    """Cancel a demo reservation.

    If confirmed=False, the tool returns a message asking for confirmation.
    If confirmed=True, marks the reservation as CANCELLED.

    Args:
        ctx: Agent run context with dependencies.
        reservation_id: Demo ticket ID to cancel.
        confirmed: Whether the cancellation has been confirmed by the user.
    """
    logger.info(
        "[demo] cancel_reservation session=%s reservation_id=%s confirmed=%s",
        ctx.deps.user_phone,
        reservation_id,
        confirmed,
    )
    session = _get_session(ctx.deps.user_phone)
    ticket = session.tickets.get(reservation_id)
    if ticket is None:
        return f"No demo reservation found with ID '{reservation_id}'."

    if ticket.status == "CANCELLED":
        return f"Reservation '{reservation_id}' is already cancelled."

    if not confirmed:
        return (
            f"Are you sure you want to cancel reservation '{reservation_id}'? "
            "Please confirm by calling this tool again with confirmed=True."
        )

    ticket.status = "CANCELLED"
    return f"Reservation '{reservation_id}' has been cancelled successfully (demo)."


async def confirm_route_modification(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
    changes: list[dict[str, Any]],
) -> str:
    """Apply slot changes to tickets in a demo route booking.

    Each entry in changes must include 'ticket_id' and 'new_slot'.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: ID of the route booking to modify.
        changes: List of dicts with 'ticket_id' and 'new_slot' keys.
    """
    logger.info(
        "[demo] confirm_route_modification session=%s route_booking_id=%s changes=%s",
        ctx.deps.user_phone,
        route_booking_id,
        changes,
    )
    session = _get_session(ctx.deps.user_phone)
    booking = session.route_bookings.get(route_booking_id)
    if booking is None:
        return f"No demo route booking found with ID '{route_booking_id}'."

    modified: list[str] = []
    for change in changes:
        ticket_id = change.get("ticket_id")
        new_slot = change.get("new_slot")
        ticket = session.tickets.get(ticket_id)
        if ticket is None:
            modified.append(f"- {ticket_id}: not found, skipped")
            continue
        ticket.slot_id = new_slot
        modified.append(f"- {ticket_id}: slot updated to {new_slot}")

    return f"Route booking '{route_booking_id}' modified (demo):\n" + "\n".join(
        modified
    )


# ---------------------------------------------------------------------------
# Demo agent tools list
# ---------------------------------------------------------------------------

DEMO_AGENT_TOOLS = [
    # Catalog & discovery
    Tool(list_experiences, prepare=_once_per_turn("list_experiences")),
    get_experience_detail,
    Tool(list_routes, prepare=_once_per_turn("list_routes")),
    get_route_detail,
    list_establishments,
    get_establishment_details,
    # Availability
    get_availability,
    list_experiences_by_availability,
    get_route_availability,
    # Date resolution
    resolve_relative_date,
    # In-memory reservations
    create_pending_reservation,
    create_route_reservation,
    get_reservation_status,
    get_route_booking_status,
    get_reservations_by_phone,
    cancel_reservation,
    confirm_route_modification,
]


# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_demo_agent: Agent[AgentDeps, str] | None = None


def reset_demo_agent() -> None:
    """Discard the singleton so the next call recreates it."""
    global _demo_agent  # noqa: PLW0603
    _demo_agent = None
    logger.info("[reset_demo_agent] Singleton discarded")


def get_demo_agent() -> Agent[AgentDeps, str]:
    """Return the singleton demo agent, creating it on first call."""
    global _demo_agent  # noqa: PLW0603
    if _demo_agent is None:
        system_prompt = _load_system_prompt()
        _demo_agent = Agent(
            model=GoogleModel.Gemini_Flash_Latest,
            system_prompt=system_prompt,
            deps_type=AgentDeps,
            tools=DEMO_AGENT_TOOLS,
            model_settings=ModelSettings(temperature=0),
        )

        @_demo_agent.instructions
        def reply_in_user_language_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            return (
                "Always reply in the same language as the user's most recent message. "
                "Ignore the language used by this system prompt, tool schemas, tool outputs, "
                "or ERP data. If any tool returns content in a different language, translate "
                "or adapt it before answering. If the user writes in Spanish, use Rioplatense Spanish."
            )

        @_demo_agent.instructions
        def current_datetime_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            now = datetime.now(tz=timezone.utc).astimezone()
            return (
                f"Current date and time: {now.strftime('%A %d %B %Y, %H:%M')} "
                f"(server timezone: {now.strftime('%Z %z')}). "
                "Use this date to resolve expressions such as tomorrow, next week, next month, "
                "or in N days."
            )

        @_demo_agent.system_prompt
        async def list_experiences_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            logger.info("[demo list_experiences_prompt] called")
            exp_list = await list_experiences(ctx)
            return json.dumps([exp.model_dump_json() for exp in exp_list])

        @_demo_agent.system_prompt
        async def list_routes_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            logger.info("[demo list_routes_prompt] called")
            route_list = await list_routes(ctx)
            return json.dumps([route.model_dump_json() for route in route_list])

        @_demo_agent.system_prompt
        async def list_establishments_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            logger.info("[demo list_establishments_prompt] called")
            establishment_list = await list_establishments(ctx)
            return json.dumps([est.model_dump_json() for est in establishment_list])

        logger.info("Demo agent initialized with %d tools", len(DEMO_AGENT_TOOLS))
    return _demo_agent

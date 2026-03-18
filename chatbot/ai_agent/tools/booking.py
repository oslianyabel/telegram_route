from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    ModificationResult,
    PendingRouteBooking,
    PendingTicket,
    ReservationsListResponse,
    ReservationStatusDetail,
    RouteBookingStatus,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data, extract_erp_error

logger = logging.getLogger(__name__)

ERP_TIMEOUT_SECONDS = 15.0


# ------------------------------------------------------------------
# 1. Create pending reservation
# ------------------------------------------------------------------


async def create_pending_reservation(
    ctx: RunContext[AgentDeps],
    experience_id: str,
    slot_id: str,
    party_size: int,
) -> PendingTicket:
    """Create a PENDING ticket reservation for an experience slot.

    The ticket expires shortly after creation. The user must confirm payment
    before it becomes CONFIRMED. Requires a resolved contact_id in deps.

    Args:
        ctx: Agent run context with dependencies.
        experience_id: ERP id of the experience to book.
        slot_id: ERP id of the slot to reserve.
        party_size: Number of people in the group.
    """
    logger.info(
        "[create_pending_reservation] contact_id=%s experience_id=%s slot_id=%s party_size=%s",
        ctx.deps.contact_id,
        experience_id,
        slot_id,
        party_size,
    )
    if not ctx.deps.contact_id:
        raise ValueError("contact_id is required in AgentDeps to create a reservation")

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.create_pending_reservation",
        json={
            "contact_id": ctx.deps.contact_id,
            "experience_id": experience_id,
            "slot_id": slot_id,
            "party_size": party_size,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    ticket = PendingTicket.model_validate(data)
    logger.info(
        "Pending ticket created: %s – expires_at=%s",
        ticket.ticket_id,
        ticket.expires_at,
    )
    return ticket


# ------------------------------------------------------------------
# 2. Get reservation status
# ------------------------------------------------------------------


async def get_reservation_status(
    ctx: RunContext[AgentDeps],
    reservation_id: str,
) -> ReservationStatusDetail:
    """Get full details and current status of a ticket by its ID.

    Use this when the user asks for information about a specific reservation.

    Args:
        ctx: Agent run context with dependencies.
        reservation_id: ERP ticket ID (e.g. "TKT-2026-03-00018").
    """
    logger.info("[get_reservation_status] reservation_id=%s", reservation_id)
    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.get_reservation_status",
        json={"reservation_id": reservation_id},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    detail = ReservationStatusDetail.model_validate(data)
    logger.info(
        "Reservation status retrieved: %s – status=%s", detail.ticket_id, detail.status
    )
    return detail


# ------------------------------------------------------------------
# 3. Get reservations by phone
# ------------------------------------------------------------------


async def get_reservations_by_phone(
    ctx: RunContext[AgentDeps],
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ReservationsListResponse:
    """List all ticket reservations associated with the current user's phone.

    The phone is taken automatically from ctx.deps.user_phone.

    Args:
        ctx: Agent run context with dependencies.
        status: Optional status filter. Allowed values: PENDING, CONFIRMED,
            CANCELLED, EXPIRED. Omit to retrieve all statuses.
        page: Page number for pagination (1-based).
        page_size: Maximum tickets to return per page (default 20).
    """
    phone = ctx.deps.user_phone
    logger.info(
        "[get_reservations_by_phone] phone=%s status=%s page=%s page_size=%s",
        phone,
        status,
        page,
        page_size,
    )
    if not phone:
        raise ValueError("user_phone is required in AgentDeps to list reservations")

    payload: dict[str, Any] = {
        "phone": phone,
        "page": page,
        "page_size": page_size,
    }
    if status:
        payload["status"] = status

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.get_reservations_by_phone",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    result = ReservationsListResponse.model_validate(data)
    logger.info("Reservations retrieved for phone=%s: total=%s", phone, result.total)
    return result


# ------------------------------------------------------------------
# 4. Confirm modification
# ------------------------------------------------------------------


async def confirm_modification(
    ctx: RunContext[AgentDeps],
    reservation_id: str,
    new_slot: str | None = None,
    party_size: int | None = None,
) -> ModificationResult:
    """Apply a confirmed modification to an existing ticket.

    At least one of new_slot or party_size must be provided.

    Args:
        ctx: Agent run context with dependencies.
        reservation_id: ERP ticket ID to modify (e.g. "TKT-2026-03-00018").
        new_slot: New slot ID to move the reservation to (optional).
        party_size: New number of people in the group (optional).
    """
    logger.info(
        "[confirm_modification] reservation_id=%s new_slot=%s party_size=%s",
        reservation_id,
        new_slot,
        party_size,
    )
    if not new_slot and not party_size:
        raise ValueError("At least one of new_slot or party_size must be provided")

    payload: dict[str, Any] = {"reservation_id": reservation_id}
    if new_slot:
        payload["new_slot"] = new_slot
    if party_size:
        payload["party_size"] = party_size

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.confirm_modification",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    result = ModificationResult.model_validate(data)
    logger.info(
        "Modification confirmed: %s – changes=%s", result.ticket_id, result.changes
    )
    return result


# ------------------------------------------------------------------
# 5. Create pending route reservation
# ------------------------------------------------------------------


async def create_route_reservation(
    ctx: RunContext[AgentDeps],
    route_id: str,
    date_from: str,
    date_to: str,
    party_size: int,
) -> PendingRouteBooking:
    """Create a PENDING route booking that bundles multiple experience tickets.

    After calling this tool, ALWAYS call get_route_booking_status with the
    returned route_booking_id to retrieve the individual ticket_id of each
    experience in the route and share them with the user.

    Args:
        ctx: Agent run context with dependencies.
        route_id: ERP id of the route to book (e.g. "ROUTE_01").
        date_from: Start date of the booking in YYYY-MM-DD format.
        date_to: End date of the booking in YYYY-MM-DD format.
        party_size: Number of people in the group.
    """
    logger.info(
        "[create_route_reservation] contact_id=%s route_id=%s date_from=%s date_to=%s party_size=%s",
        ctx.deps.contact_id,
        route_id,
        date_from,
        date_to,
        party_size,
    )
    if not ctx.deps.contact_id:
        raise ValueError(
            "contact_id is required in AgentDeps to create a route reservation"
        )

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.route_booking_controller.create_route_reservation",
        json={
            "contact_id": ctx.deps.contact_id,
            "route_id": route_id,
            "date_from": date_from,
            "date_to": date_to,
            "party_size": party_size,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        erp_message = extract_erp_error(response.json())
        logger.error(
            "[create_route_reservation] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        raise ModelRetry(
            f"ERP rejected the route reservation ({response.status_code}): {erp_message}. "
            "Try a different date or route_id."
        )
    data: dict[str, Any] = extract_erp_data(response.json())

    booking = PendingRouteBooking.model_validate(data)
    logger.info(
        "Pending route booking created: %s – tickets=%s",
        booking.route_booking_id,
        booking.tickets,
    )
    return booking


# ------------------------------------------------------------------
# 6. Get route booking status
# ------------------------------------------------------------------


async def get_route_booking_status(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
) -> RouteBookingStatus:
    """Get the status of a route booking including the ticket_id of each experience.

    Call this immediately after create_route_reservation to obtain the
    individual ticket IDs that must be shared with the user.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: ERP route booking ID (e.g. "RB-2026-03-00013").
    """
    logger.info("[get_route_booking_status] route_booking_id=%s", route_booking_id)

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.route_booking_controller.get_route_status",
        json={"route_booking_id": route_booking_id},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    status = RouteBookingStatus.model_validate(data)
    logger.info(
        "Route booking status retrieved: %s – status=%s tickets=%s",
        status.route_booking_id,
        status.status,
        [t.ticket_id for t in status.tickets],
    )
    return status

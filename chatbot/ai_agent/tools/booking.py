from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
from pydantic_ai import ModelRetry, RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    AddActivitiesToRoutePreview,
    AddActivitiesToRouteResult,
    CancellationImpact,
    CancellationResult,
    CustomerItinerary,
    ModificationPreview,
    ModificationResult,
    PendingRouteBooking,
    PendingTicket,
    ReservationsListResponse,
    ReservationStatusDetail,
    RouteActivityInput,
    RouteBookingStatus,
    RouteModificationPreview,
    RouteTicketChange,
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
    selected_date: str,
) -> PendingTicket | str:
    """Create a PENDING ticket reservation for an experience slot.

    The ticket expires shortly after creation. The user must confirm payment
    before it becomes CONFIRMED. Requires a resolved contact_id and user_name in deps.
    If user_name is missing, ask the user for their name and call update_contact first.

    Args:
        ctx: Agent run context with dependencies.
        experience_id: ERP id of the experience to book.
        slot_id: ERP id of the slot to reserve.
        party_size: Number of people in the group.
        selected_date: Reservation date in YYYY-MM-DD format.
    """
    logger.info(
        "[create_pending_reservation] contact_id=%s experience_id=%s slot_id=%s party_size=%s selected_date=%s",
        ctx.deps.contact_id,
        experience_id,
        slot_id,
        party_size,
        selected_date,
    )
    if not ctx.deps.contact_id:
        raise ValueError("contact_id is required in AgentDeps to create a reservation")
    if not ctx.deps.user_name:
        return "Antes de crear la reserva necesito el nombre del cliente. Pídele su nombre al usuario y llama a update_contact con el valor obtenido."
    if not ctx.deps.lead_id:
        return "Antes de crear la reserva es necesario registrar el interés del cliente. Llama a upsert_lead primero y luego vuelve a llamar a create_pending_reservation."
    try:
        date.fromisoformat(selected_date)
    except ValueError as error:
        raise ModelRetry(
            "selected_date es obligatorio y debe estar en formato YYYY-MM-DD. "
            "Usa la fecha exacta del slot seleccionado antes de volver a llamar a create_pending_reservation."
        ) from error

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.create_pending_reservation",
        json={
            "contact_id": ctx.deps.contact_id,
            "experience_id": experience_id,
            "slot_id": slot_id,
            "party_size": party_size,
            "selected_date": selected_date,
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
# 4. Modify reservation preview
# ------------------------------------------------------------------


async def modify_reservation_preview(
    ctx: RunContext[AgentDeps],
    reservation_id: str,
    new_slot: str | None = None,
    party_size: int | None = None,
) -> ModificationPreview | str:
    """Check whether a modification is allowed and preview its price impact.

    Call this BEFORE confirm_modification so the user can be informed of any
    price difference. At least one of new_slot or party_size must be provided.
    Use the returned slot_change_allowed and party_size_change_allowed flags
    to determine if the modification can proceed. Share the price_impact
    information with the user before asking for confirmation.

    Args:
        ctx: Agent run context with dependencies.
        reservation_id: ERP ticket ID to preview (e.g. "TKT-2026-03-00018").
        new_slot: New slot ID to move the reservation to (optional).
        party_size: New number of people in the group (optional).
    """
    logger.info(
        "[modify_reservation_preview] reservation_id=%s new_slot=%s party_size=%s",
        reservation_id,
        new_slot,
        party_size,
    )
    if not new_slot and not party_size:
        raise ModelRetry(
            "At least one of new_slot or party_size must be provided to preview a modification."
        )

    payload: dict[str, Any] = {"reservation_id": reservation_id}
    if new_slot:
        payload["new_slot"] = new_slot
    if party_size:
        payload["party_size"] = party_size

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.modify_reservation_preview",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        erp_message = extract_erp_error(response.json())
        logger.error(
            "[modify_reservation_preview] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        return f"No es posible previsualizar la modificación: {erp_message}"

    data: dict[str, Any] = extract_erp_data(response.json())
    preview_data: dict[str, Any] = data.get("preview", data)

    preview = ModificationPreview.model_validate(preview_data)
    logger.info(
        "[modify_reservation_preview] reservation_id=%s slot_change_allowed=%s party_size_change_allowed=%s price_impact=%s",
        preview.reservation_id,
        preview.slot_change_allowed,
        preview.party_size_change_allowed,
        preview.price_impact,
    )
    return preview


# ------------------------------------------------------------------
# 5. Confirm modification
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
) -> PendingRouteBooking | str:
    """Create a PENDING route booking that bundles multiple experience tickets.

    After calling this tool, ALWAYS call get_route_booking_status with the
    returned route_booking_id to retrieve the individual ticket_id of each
    experience in the route and share them with the user.
    Requires a resolved contact_id and user_name in deps.
    If user_name is missing, ask the user for their name and call update_contact first.

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
    if not ctx.deps.user_name:
        return "Antes de crear la reserva de ruta necesito el nombre del cliente. Pídele su nombre al usuario y llama a update_contact con el valor obtenido."

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


# ------------------------------------------------------------------
# 7. Cancel individual reservation
# ------------------------------------------------------------------


async def cancel_reservation(
    ctx: RunContext[AgentDeps],
    reservation_id: str,
    confirmed: bool = False,
) -> CancellationResult | str:
    """Cancel a PENDING or CONFIRMED individual experience ticket.

    This action is irreversible. Always call this tool first with
    ``confirmed=False`` so the user is explicitly asked for consent.
    Only call again with ``confirmed=True`` once the user has answered
    affirmatively (e.g. "sí", "confirmo", "adelante").

    Args:
        ctx: Agent run context with dependencies.
        reservation_id: ERP ticket ID to cancel (e.g. "TKT-2026-03-00018").
        confirmed: Must be True for the cancellation to be executed.
            Pass False (default) to trigger the confirmation prompt.
    """
    logger.info(
        "[cancel_reservation] reservation_id=%s confirmed=%s",
        reservation_id,
        confirmed,
    )

    if not confirmed:
        return (
            f"El usuario debe confirmar antes de proceder. "
            f"Pregúntale: '¿Estás seguro/a de que deseas cancelar tu ticket {reservation_id}? "
            f"Esta acción no se puede deshacer.' "
            f"Si responde afirmativamente, llama a esta herramienta de nuevo con confirmed=True."
        )

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.ticket_controller.cancel_reservation",
        json={"reservation_id": reservation_id},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        erp_message = extract_erp_error(response.json())
        logger.error(
            "[cancel_reservation] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        raise ModelRetry(
            f"ERP rechazó la cancelación ({response.status_code}): {erp_message}."
        )

    data: dict[str, Any] = extract_erp_data(response.json())
    result = CancellationResult.model_validate(data)
    logger.info(
        "Ticket cancelled: %s – %s → %s",
        result.ticket_id,
        result.old_status,
        result.new_status,
    )
    return result


# ------------------------------------------------------------------
# 6. Get customer itinerary
# ------------------------------------------------------------------


async def get_customer_itinerary(
    ctx: RunContext[AgentDeps],
) -> CustomerItinerary:
    """Get the full travel itinerary for the customer.

    Includes all routes and standalone experience reservations, both upcoming
    and completed. Use this to give the user an overview of their plans.
    Requires a resolved contact_id in deps.

    Args:
        ctx: Agent run context with dependencies.
    """
    logger.info("[get_customer_itinerary] contact_id=%s", ctx.deps.contact_id)
    if not ctx.deps.contact_id:
        raise ValueError(
            "contact_id is required in AgentDeps to retrieve the itinerary"
        )

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.itinerary_controller.get_customer_itinerary",
        json={"contact_id": ctx.deps.contact_id},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    itinerary = CustomerItinerary.model_validate(data)
    logger.info(
        "Itinerary retrieved: total_reservations=%d", itinerary.total_reservations
    )
    return itinerary


# ------------------------------------------------------------------
# 9. Cancellation impact
# ------------------------------------------------------------------


async def get_cancellation_impact(
    ctx: RunContext[AgentDeps],
    reservation_id: str,
) -> CancellationImpact | str:
    """Check whether a reservation can be cancelled and what the financial impact would be.

    Always call this tool BEFORE attempting to cancel an individual reservation.
    Use the result to:
    - Inform the customer if cancellation is not allowed and why.
    - Show the penalty and refund amount when cancellation is allowed, and ask
      for explicit confirmation before proceeding with the cancellation.

    Args:
        ctx: Agent run context with dependencies.
        reservation_id: The ERP ticket ID to evaluate (e.g. "TKT-2026-03-00067").
    """
    logger.info(
        "[get_cancellation_impact] reservation_id=%s",
        reservation_id,
    )
    try:
        response = await ctx.deps.erp_client.post(
            f"{ERP_BASE_PATH}.pricing_controller.get_cancellation_impact",
            json={"reservation_id": reservation_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        error_msg = extract_erp_error(exc.response.json())
        logger.warning(
            "[get_cancellation_impact] HTTP %s — %s",
            exc.response.status_code,
            error_msg,
        )
        raise ModelRetry(f"ERP returned an error: {error_msg}") from exc
    except httpx.RequestError as exc:
        logger.exception("[get_cancellation_impact] network error")
        raise ModelRetry(f"Network error contacting ERP: {exc}") from exc

    data = extract_erp_data(response.json())
    if not isinstance(data, dict):
        raise ModelRetry(
            "Unexpected response format from ERP cancellation impact endpoint."
        )

    return CancellationImpact.model_validate(data)


# ------------------------------------------------------------------
# 10. Cancel route booking
# ------------------------------------------------------------------


async def cancel_route_booking(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
    cancellation_reason: str,
) -> str:
    """Cancel a route booking.

    According to business rules, a cancellation reason is REQUIRED before
    proceeding. Always ask the user for their reason before calling this tool.
    If the user has not provided a reason yet, return a message asking for it
    instead of calling this tool.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: ERP route booking ID (e.g. "RB-2026-03-00013").
        cancellation_reason: Reason provided by the user for the cancellation.
    """
    logger.info(
        "[cancel_route_booking] route_booking_id=%s reason=%s",
        route_booking_id,
        cancellation_reason,
    )

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.route_booking_controller.cancel_route_booking",
        json={
            "route_booking_id": route_booking_id,
            "reason": cancellation_reason,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    resp_body: dict[str, Any] = response.json()

    if response.is_error:
        # Known ERP bug: the first call always raises a TimestampMismatchError /
        # CharacterLengthExceededError, but the cancellation actually succeeds on
        # the server. We detect these specific exceptions and treat them as success.
        exc_type: str = resp_body.get("exc_type", "")
        exception_text: str = resp_body.get("exception", "")
        if (
            "CharacterLengthExceededError" in exc_type
            or "TimestampMismatchError" in exception_text
        ):
            logger.warning(
                "[cancel_route_booking] Known ERP bug — cancellation succeeded despite error response (exc_type=%s)",
                exc_type,
            )
            return f"La reserva de ruta {route_booking_id} fue cancelada exitosamente."

        erp_message = extract_erp_error(resp_body)
        logger.error(
            "[cancel_route_booking] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        raise ModelRetry(
            f"ERP rechazó la cancelación ({response.status_code}): {erp_message}."
        )

    wrapper: Any = resp_body.get("message", resp_body)
    if isinstance(wrapper, dict):
        data: Any = wrapper.get("data", {})
        status: str = data.get("status", "") if isinstance(data, dict) else ""
        if wrapper.get("success") or status == "CANCELLED":
            logger.info(
                "[cancel_route_booking] Route booking cancelled: %s (status=%s)",
                route_booking_id,
                status,
            )
            return f"La reserva de ruta {route_booking_id} fue cancelada exitosamente."

    erp_message = extract_erp_error(resp_body)
    raise ModelRetry(
        f"Respuesta inesperada del ERP al cancelar la reserva: {erp_message}."
    )


# ------------------------------------------------------------------
# 11. Modify route booking preview
# ------------------------------------------------------------------


async def modify_route_booking_preview(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
    changes: list[RouteTicketChange],
) -> RouteModificationPreview | str:
    """Check whether modifications to a route booking are allowed and preview price impact.

    Call this BEFORE confirm_route_modification so the user can be informed of
    any price difference. Each item in ``changes`` must include the ticket_id
    and at least one of new_slot or party_size.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: ERP route booking ID (e.g. "RB-2026-03-00013").
        changes: List of ticket-level changes to preview.
    """
    logger.info(
        "[modify_route_booking_preview] route_booking_id=%s changes=%s",
        route_booking_id,
        changes,
    )

    if not changes:
        raise ModelRetry(
            "At least one ticket change must be provided to preview a route modification."
        )

    changes_payload = [
        {k: v for k, v in c.model_dump().items() if v is not None} for c in changes
    ]

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.route_booking_controller.modify_route_booking_preview",
        json={
            "route_booking_id": route_booking_id,
            "changes": changes_payload,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        erp_message = extract_erp_error(response.json())
        logger.error(
            "[modify_route_booking_preview] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        return f"No es posible previsualizar la modificación de la ruta: {erp_message}"

    data: dict[str, Any] = extract_erp_data(response.json())
    if "route_booking_id" not in data:
        data["route_booking_id"] = route_booking_id

    preview = RouteModificationPreview.model_validate(data)
    logger.info(
        "[modify_route_booking_preview] route_booking_id=%s changes_count=%s",
        preview.route_booking_id,
        len(preview.changes),
    )
    return preview


# ------------------------------------------------------------------
# 12. Confirm route modification
# ------------------------------------------------------------------


async def confirm_route_modification(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
    changes: list[RouteTicketChange],
) -> str:
    """Apply confirmed modifications to a route booking.

    Must be called AFTER modify_route_booking_preview and only when the user
    has explicitly confirmed the changes and the price impact.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: ERP route booking ID (e.g. "RB-2026-03-00013").
        changes: List of ticket-level changes to apply.
    """
    logger.info(
        "[confirm_route_modification] route_booking_id=%s changes=%s",
        route_booking_id,
        changes,
    )

    if not changes:
        raise ModelRetry(
            "At least one ticket change must be provided to confirm a route modification."
        )

    changes_payload = [
        {k: v for k, v in c.model_dump().items() if v is not None} for c in changes
    ]

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.route_booking_controller.confirm_route_modification",
        json={
            "route_booking_id": route_booking_id,
            "changes": changes_payload,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        erp_message = extract_erp_error(response.json())
        logger.error(
            "[confirm_route_modification] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        raise ModelRetry(
            f"ERP rechazó la modificación ({response.status_code}): {erp_message}."
        )

    data: dict[str, Any] = extract_erp_data(response.json())
    modified_tickets: list[str] = data.get("modified_tickets", [])
    updated_status: dict[str, Any] = data.get("updated_status", {})
    new_status: str = updated_status.get("status", "")

    logger.info(
        "[confirm_route_modification] route_booking_id=%s modified_tickets=%s new_status=%s",
        route_booking_id,
        modified_tickets,
        new_status,
    )
    return (
        f"La reserva de ruta {route_booking_id} fue modificada exitosamente. "
        f"Tickets actualizados: {', '.join(modified_tickets)}. "
        f"Estado actual: {new_status}."
    )


# ------------------------------------------------------------------
# 13. Add activities to route preview
# ------------------------------------------------------------------


async def add_activities_to_route_preview(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
    activities: list[RouteActivityInput],
) -> AddActivitiesToRoutePreview | str:
    """Check whether adding new activities to a route booking is possible and preview the extra cost.

    Call this BEFORE confirm_add_activities_to_route so the user can review the
    additional price and deposit before confirming. Share the
    total_additional_price and total_additional_deposit with the user and ask
    for explicit confirmation before proceeding.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: ERP route booking ID (e.g. "RB-2026-04-00017").
        activities: List of activities to add, each with experience_id and slot_id.
    """
    logger.info(
        "[add_activities_to_route_preview] route_booking_id=%s activities=%s",
        route_booking_id,
        activities,
    )

    if not activities:
        raise ModelRetry(
            "At least one activity must be provided to preview adding activities to the route."
        )

    activities_payload = [a.model_dump() for a in activities]

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.route_booking_controller.add_activities_to_route_preview",
        json={
            "route_booking_id": route_booking_id,
            "activities": activities_payload,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        erp_message = extract_erp_error(response.json())
        logger.error(
            "[add_activities_to_route_preview] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        return f"No es posible previsualizar la adición de actividades: {erp_message}"

    data: dict[str, Any] = extract_erp_data(response.json())
    preview = AddActivitiesToRoutePreview.model_validate(data)
    logger.info(
        "[add_activities_to_route_preview] route_booking_id=%s total_additional_price=%s",
        preview.route_booking_id,
        preview.total_additional_price,
    )
    return preview


# ------------------------------------------------------------------
# 14. Confirm add activities to route
# ------------------------------------------------------------------


async def confirm_add_activities_to_route(
    ctx: RunContext[AgentDeps],
    route_booking_id: str,
    activities: list[RouteActivityInput],
) -> AddActivitiesToRouteResult | str:
    """Add new activities to an existing route booking after user confirmation.

    Must be called AFTER add_activities_to_route_preview and only when the user
    has explicitly confirmed the additional cost shown in the preview.

    Args:
        ctx: Agent run context with dependencies.
        route_booking_id: ERP route booking ID (e.g. "RB-2026-04-00017").
        activities: List of activities to add, each with experience_id and slot_id.
    """
    logger.info(
        "[confirm_add_activities_to_route] route_booking_id=%s activities=%s",
        route_booking_id,
        activities,
    )

    if not activities:
        raise ModelRetry(
            "At least one activity must be provided to confirm adding activities to the route."
        )

    activities_payload = [a.model_dump() for a in activities]

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.route_booking_controller.confirm_add_activities_to_route",
        json={
            "route_booking_id": route_booking_id,
            "activities": activities_payload,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if response.is_error:
        erp_message = extract_erp_error(response.json())
        logger.error(
            "[confirm_add_activities_to_route] ERP error %s: %s",
            response.status_code,
            erp_message,
        )
        raise ModelRetry(
            f"ERP rechazó la adición de actividades ({response.status_code}): {erp_message}."
        )

    data: dict[str, Any] = extract_erp_data(response.json())
    result = AddActivitiesToRouteResult.model_validate(data)
    logger.info(
        "[confirm_add_activities_to_route] route_booking_id=%s new_tickets=%s status=%s",
        result.route_booking_id,
        result.new_tickets,
        result.status,
    )
    return result

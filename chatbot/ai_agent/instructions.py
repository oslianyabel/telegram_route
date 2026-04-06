from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    ContactInfo,
    CustomerItinerary,
    PaymentInstructions,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data

logger = logging.getLogger(__name__)
ERP_TIMEOUT_SECONDS = 15.0


async def resolve_or_create_contact(
    ctx: RunContext[AgentDeps],
) -> str:
    """retrieves the customer's data

    Args:
        ctx: Agent run context with dependencies.
    """
    resolved_phone: str = ctx.deps.user_phone or ""
    if not resolved_phone:
        raise ValueError("No phone available to resolve contact")

    payload: dict[str, Any] = {"phone": resolved_phone}

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.contact_controller.resolve_or_create_contact",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())

    contact = ContactInfo.model_validate(data)
    ctx.deps.contact_id = contact.contact_id

    """ is_real_name: bool = bool(
        contact.name and contact.name != contact.phone and contact.name != contact.email
    ) """

    if contact.name:
        ctx.deps.user_name = contact.name
    if contact.email:
        ctx.deps.user_email = contact.email
    if contact.phone and ctx.deps.user_phone is None:  # Telegram
        ctx.deps.user_phone = contact.phone

    lines: list[str] = [
        "## Customer data",
    ]
    if ctx.deps.user_name:
        lines.append(f"Name: {ctx.deps.user_name}.")
    if ctx.deps.user_email:
        lines.append(f"Email: {ctx.deps.user_email}.")
    if ctx.deps.user_phone:
        lines.append(f"Phone: {ctx.deps.user_phone}.")

    return "\n".join(lines)


async def get_current_itinerary_context(
    ctx: RunContext[AgentDeps],
) -> str:
    """Retrieves the customer's itinerary to provide context.

    Args:
        ctx: Agent run context with dependencies.
    """
    if not ctx.deps.contact_id:
        return "No itinerary information is available yet (contact not resolved)."

    try:
        response = await ctx.deps.erp_client.post(
            f"{ERP_BASE_PATH}.itinerary_controller.get_customer_itinerary",
            json={"contact_id": ctx.deps.contact_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data: dict[str, Any] = extract_erp_data(response.json())
        itinerary = CustomerItinerary.model_validate(data)

        if itinerary.total_reservations == 0:
            return "The customer has no reservations or itinerary records."

        lines = [f"## Customer itinerary (Total: {itinerary.total_reservations})"]
        for item in itinerary.itinerary:
            item_title = (
                f"Route: {item.route_name}" if item.type == "route" else "Experience"
            )
            lines.append(f"- {item_title} ({len(item.reservations)} paradas/tickets):")
            for res in item.reservations:
                lines.append(
                    f"  * {res.reservation_id}: {res.experience_name} - {res.date} {res.time} [{res.status}]"
                )

        return "\n".join(lines)
    except Exception as e:
        logger.error("Error retrieving itinerary context: %s", e)
        return "Error retrieving the customer's itinerary."


async def get_pending_deposit_context(ctx: RunContext[AgentDeps]) -> str:
    """Retrieves pending deposit information for confirmed tickets.

    This instruction is active on every turn. It returns deposit details when
    the customer has a confirmed ticket with an outstanding deposit, and returns
    an empty string once all deposits are paid (effectively deactivating itself).

    Args:
        ctx: Agent run context with dependencies.
    """
    if not ctx.deps.contact_id:
        return ""

    try:
        itinerary_resp = await ctx.deps.erp_client.post(
            f"{ERP_BASE_PATH}.itinerary_controller.get_customer_itinerary",
            json={"contact_id": ctx.deps.contact_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        itinerary_resp.raise_for_status()
        itinerary = CustomerItinerary.model_validate(
            extract_erp_data(itinerary_resp.json())
        )
    except Exception as e:
        logger.error("Error retrieving itinerary for deposit context: %s", e)
        return ""

    confirmed_ticket_ids: list[str] = [
        reservation.reservation_id
        for item in itinerary.itinerary
        for reservation in item.reservations
        if reservation.status.lower() == "confirmed"
    ]

    if not confirmed_ticket_ids:
        return ""

    pending_deposits: list[str] = []
    for ticket_id in confirmed_ticket_ids:
        try:
            dep_resp = await ctx.deps.erp_client.post(
                f"{ERP_BASE_PATH}.deposit_controller.get_deposit_instructions",
                json={"ticket_id": ticket_id},
                timeout=ERP_TIMEOUT_SECONDS,
            )
            if dep_resp.is_error:
                continue
            instructions = PaymentInstructions.model_validate(
                extract_erp_data(dep_resp.json())
            )
            if (
                instructions.amount_remaining is not None
                and instructions.amount_remaining > 0
            ):
                line = (
                    f"- Ticket {instructions.ticket_id}: "
                    f"remaining {instructions.amount_remaining} UYU"
                    f" (required {instructions.amount_required} UYU, paid {instructions.amount_paid or 0} UYU)"
                )
                if instructions.due_at:
                    line += f", due {instructions.due_at}"
                if instructions.instructions:
                    line += f"\n  Payment details: {instructions.instructions}"
                pending_deposits.append(line)
        except Exception as e:
            logger.warning("Error checking deposit for ticket %s: %s", ticket_id, e)
            continue

    if not pending_deposits:
        return ""

    lines = [
        "## Pending deposit payments",
        "The customer has confirmed tickets with outstanding deposit payments:",
        *pending_deposits,
        "",
        "Guide the customer to complete the deposit payment. "
        "Once payment is confirmed, this context will no longer appear.",
    ]
    return "\n".join(lines)

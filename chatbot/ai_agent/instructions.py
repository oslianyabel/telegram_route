from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    ContactInfo,
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
        "## Datos del cliente",
    ]
    if ctx.deps.user_name:
        lines.append(f"Nombre: {ctx.deps.user_name}.")
    if ctx.deps.user_email:
        lines.append(f"Email: {ctx.deps.user_email}.")
    if ctx.deps.user_phone:
        lines.append(f"Teléfono: {ctx.deps.user_phone}.")

    return "\n".join(lines)

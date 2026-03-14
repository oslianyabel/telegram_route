from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import ModelRetry, RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    LeadInfo,
    UpdateContactResult,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data

logger = logging.getLogger(__name__)

ERP_TIMEOUT_SECONDS = 15.0


# ------------------------------------------------------------------
# 1. Contact (contact_controller) – tools
# ------------------------------------------------------------------


async def update_contact(
    ctx: RunContext[AgentDeps],
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> UpdateContactResult | str:
    """Update one or more fields of the current contact.

    Pass only the fields you want to change; omit those that already have the correct value.

    Args:
        ctx: Agent run context with dependencies.
        name: New display name (only if it differs from the current one).
        email: New email address (only if it differs from the current one).
        phone: New phone number (use with caution – changes the dedup key).
    """
    logger.info(
        "[update_contact] contact_id=%s name=%s email=%s phone=%s",
        ctx.deps.contact_id,
        name,
        email,
        phone,
    )
    if not ctx.deps.contact_id:
        raise ValueError("contact_id is required in AgentDeps to update a contact")

    if not any([name, email, phone]):
        return "No fields to update. Provide at least one of name, email, or phone."

    payload: dict[str, Any] = {"contact_id": ctx.deps.contact_id}
    if name is not None:
        payload["name"] = name
    if email is not None:
        payload["email"] = email
    if phone is not None:
        payload["phone"] = phone

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.contact_controller.update_contact",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data: dict[str, Any] = extract_erp_data(response.json())
    result = UpdateContactResult.model_validate(data)

    # Keep deps in sync with the updated values
    updated_contact = result.contact
    is_real_name = bool(
        updated_contact.name
        and updated_contact.name != updated_contact.phone
        and updated_contact.name != (updated_contact.email or "")
    )
    if is_real_name:
        ctx.deps.user_name = updated_contact.name
    if updated_contact.email:
        ctx.deps.user_email = updated_contact.email
    if updated_contact.phone and ctx.deps.user_phone is None:  # Telegram
        ctx.deps.user_phone = updated_contact.phone

    logger.info(
        "Contact updated: %s – changed fields: %s",
        ctx.deps.contact_id,
        result.changed_fields,
    )
    return result


# ------------------------------------------------------------------
# 3. Leads (lead_controller)
# ------------------------------------------------------------------


async def upsert_lead(
    ctx: RunContext[AgentDeps],
    interest_type: str = "Experience",
) -> LeadInfo:
    """Create or update a CRM lead for the current contact.

    Called whenever a user shows commercial intent (asks about prices,
    availability, or booking) without completing a reservation. The ERP
    consolidates leads per contact to avoid duplicates.

    Args:
        ctx: Agent run context with dependencies.
        interest_type: Category of interest (e.g. "Experience", "Route").
    """
    logger.info(
        "[upsert_lead] contact_id=%s conversation_id=%s interest_type=%s",
        ctx.deps.contact_id,
        ctx.deps.conversation_id,
        interest_type,
    )
    if not ctx.deps.contact_id:
        raise ValueError("contact_id is required in AgentDeps to upsert a lead")

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.lead_controller.upsert_lead",
        json={
            "contact_id": ctx.deps.contact_id,
            "interest_type": interest_type,
            "status": "OPEN",
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )

    # ERP bug: returns VALIDATION_ERROR when the lead is already CONVERTED.
    # See context/api_issues.md for details.
    if not response.is_success:
        try:
            body: dict[str, Any] = response.json()
        except Exception:
            body = {}
        error_msg: str = (
            body.get("message", {}).get("error", {}).get("message", "")
            if isinstance(body.get("message"), dict)
            else ""
        )
        if "CONVERTED" in error_msg:
            logger.info(
                "[upsert_lead] Lead already CONVERTED for contact_id=%s — skipping.",
                ctx.deps.contact_id,
            )
            raise ModelRetry(
                "El lead de este contacto ya fue convertido. "
                "No es necesario crear un nuevo lead. Continua con la conversacion normalmente."
            )
        response.raise_for_status()

    data: dict[str, Any] = extract_erp_data(response.json())

    lead = LeadInfo.model_validate(data)
    logger.info("Lead upserted: %s – status=%s", lead.lead_id, lead.status)
    return lead

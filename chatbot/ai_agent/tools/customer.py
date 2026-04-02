from __future__ import annotations

import logging
from typing import Any

import httpx
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


def _extract_erp_error_message(response: httpx.Response) -> str:
    """Return the ERP validation message when present."""
    try:
        body = response.json()
    except ValueError:
        return ""

    message = body.get("message")
    if not isinstance(message, dict):
        return ""

    error = message.get("error")
    if not isinstance(error, dict):
        return ""

    error_message = error.get("message", "")
    return error_message if isinstance(error_message, str) else ""


def _is_duplicate_contact_name_error(error_message: str) -> bool:
    """Detect the ERP validation error raised for duplicated contact names."""
    normalized_message = error_message.lower()
    return (
        "another cheese contact with name" in normalized_message
        and "select another name" in normalized_message
    )


# ------------------------------------------------------------------
# 1. Contact (contact_controller) – tools
# ------------------------------------------------------------------


async def update_contact(
    ctx: RunContext[AgentDeps],
    name: str | None = None,
    email: str | None = None,
    preferred_language: str | None = None,
) -> UpdateContactResult | str:
    """Update one or more fields of the current contact.

    Pass only the fields you want to change; omit those that already have the correct value.
    Always detect the language the user is writing in and pass it as ``preferred_language``
    (e.g. "Spanish", "English", "French", "Portuguese", "German").

    Args:
        ctx: Agent run context with dependencies.
        name: New display name (only if it differs from the current one).
        email: New email address (only if it differs from the current one).
        preferred_language: Language detected from the user's messages (e.g. "Spanish", "English").
    """
    logger.info(
        "[update_contact] contact_id=%s name=%s email=%s preferred_language=%s",
        ctx.deps.contact_id,
        name,
        email,
        preferred_language,
    )
    if not ctx.deps.contact_id:
        raise ValueError("contact_id is required in AgentDeps to update a contact")

    if not any([name, email, preferred_language]):
        return "No fields to update. Provide at least one of name, email or preferred_language."

    payload: dict[str, Any] = {"contact_id": ctx.deps.contact_id}
    if name is not None:
        payload["name"] = name
    if email is not None:
        payload["email"] = email
    if preferred_language is not None:
        payload["preferred_language"] = preferred_language

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.contact_controller.update_contact",
        json=payload,
        timeout=ERP_TIMEOUT_SECONDS,
    )
    if not response.is_success:
        error_message = _extract_erp_error_message(response)
        if name and _is_duplicate_contact_name_error(error_message):
            logger.info(
                "[update_contact] duplicate name rejected for contact_id=%s name=%s",
                ctx.deps.contact_id,
                name,
            )
            raise ModelRetry(
                "The ERP does not allow saving a name that is already used by another contact. "
                "Do not repeat that same name. "
                "Ask the user for their full name or a more specific name "
                "and call update_contact again with that new value. "
                "If you also need to save email or preferred_language, "
                "do it in a separate call without sending name."
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
        error_msg = _extract_erp_error_message(response)
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

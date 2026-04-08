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


def _is_contact_already_exists_error(error_message: str) -> bool:
    """Detect the ERP error when updating with data that already belongs to the contact."""
    return "contact with this phone or email already exists" in error_message.lower()


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
    if name is not None and name != ctx.deps.user_name:
        payload["name"] = name
    if email is not None and email != ctx.deps.user_email:
        payload["email"] = email
    if preferred_language is not None:
        payload["preferred_language"] = preferred_language

    if len(payload) == 1:  # solo contact_id, nada que actualizar
        return "No fields to update — all provided values already match the current contact data."

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
        if _is_contact_already_exists_error(error_message):
            logger.info(
                "[update_contact] contact already has this data contact_id=%s email=%s",
                ctx.deps.contact_id,
                email,
            )
            # Sync deps so future calls skip the duplicate field
            if email:
                ctx.deps.user_email = email
            remaining_payload: dict[str, Any] = {"contact_id": ctx.deps.contact_id}
            if "preferred_language" in payload:
                remaining_payload["preferred_language"] = payload["preferred_language"]
            if "name" in payload:
                remaining_payload["name"] = payload["name"]
            if len(remaining_payload) > 1:
                retry_response = await ctx.deps.erp_client.post(
                    f"{ERP_BASE_PATH}.contact_controller.update_contact",
                    json=remaining_payload,
                    timeout=ERP_TIMEOUT_SECONDS,
                )
                if retry_response.is_success:
                    data: dict[str, Any] = extract_erp_data(retry_response.json())
                    return UpdateContactResult.model_validate(data)
            return "The contact data is already up to date in the ERP. No changes were needed."
        logger.error(
            "[update_contact] ERP error contact_id=%s status=%s body=%s",
            ctx.deps.contact_id,
            response.status_code,
            response.text[:300],
        )
        return (
            f"The ERP returned a {response.status_code} error when updating the contact. "
            "Inform the user that there was a temporary technical issue and ask them to try again in a few minutes."
        )
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
) -> LeadInfo | str:
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
            ctx.deps.lead_id = "CONVERTED"
            return "El lead de este contacto ya fue convertido. Continua con create_pending_reservation."
        # A 422 that is not CONVERTED is likely a race condition: two concurrent calls
        # hit the ERP simultaneously for the same contact (e.g. _ensure_lead inside a
        # catalog tool running in parallel with an explicit upsert_lead call by the model).
        # One call succeeds and sets ctx.deps.lead_id; the other gets a constraint error.
        # Treating this as non-fatal keeps the agent running correctly.
        logger.warning(
            "[upsert_lead] unexpected ERP %s for contact_id=%s — likely a concurrent call, treating as non-fatal. msg=%s",
            response.status_code,
            ctx.deps.contact_id,
            error_msg,
        )
        return (
            f"A lead already exists or was just created for contact {ctx.deps.contact_id}. "
            "Continue with the next step."
        )

    data: dict[str, Any] = extract_erp_data(response.json())

    lead = LeadInfo.model_validate(data)
    if lead.lead_id:
        ctx.deps.lead_id = lead.lead_id
    logger.info("Lead upserted: %s – status=%s", lead.lead_id, lead.status)
    return lead

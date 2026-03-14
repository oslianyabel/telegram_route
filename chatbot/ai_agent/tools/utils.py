from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    ConversationInfo,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data

logger = logging.getLogger(__name__)

ERP_TIMEOUT_SECONDS = 15.0

_ALREADY_EXISTS_MSG = "An active conversation already exists"


async def open_or_resume_conversation(
    ctx: RunContext[AgentDeps],
    channel: str = "WhatsApp",
) -> ConversationInfo | None:
    """Open a new conversation or resume the active one for the current contact.

    Returns ``None`` when the ERP reports that an active conversation already
    exists but does not return its ID (known ERP bug — see api_issues.md).

    Args:
        ctx: Agent run context with dependencies.
        channel: Communication channel (default "WhatsApp").
    """
    logger.debug(
        "[open_or_resume_conversation] contact_id=%s channel=%s",
        ctx.deps.contact_id,
        channel,
    )
    if not ctx.deps.contact_id:
        raise ValueError("contact_id is required in AgentDeps to open a conversation")

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.conversation_controller.open_or_resume_conversation",
        json={
            "contact_id": ctx.deps.contact_id,
            "channel": channel,
            "status": "ACTIVE",
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )

    # ERP bug: returns VALIDATION_ERROR instead of the existing conversation.
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
        if _ALREADY_EXISTS_MSG in error_msg:
            logger.warning(
                "[open_or_resume_conversation] ERP bug: conversation already exists "
                "for contact_id=%s but ERP did not return conversation_id.",
                ctx.deps.contact_id,
            )
            return None
        response.raise_for_status()

    data: dict[str, Any] = extract_erp_data(response.json())
    conversation = ConversationInfo.model_validate(data)
    ctx.deps.conversation_id = conversation.conversation_id
    logger.debug(
        "Conversation opened: %s (is_new=%s)",
        conversation.conversation_id,
        conversation.is_new,
    )
    return conversation

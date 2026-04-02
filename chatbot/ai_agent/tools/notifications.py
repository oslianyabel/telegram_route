from __future__ import annotations

import logging

from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.reminders.lead_followup import FOLLOW_UP_OPTOUT_MARKER

logger = logging.getLogger(__name__)


async def stop_lead_followups(ctx: RunContext[AgentDeps]) -> str:
    """Disable future lead follow-up messages for this conversation.

    Use this tool only when the user explicitly asks not to receive more
    reminders, follow-up messages, or promotional nudges to resume booking.

    Args:
        ctx: Agent run context with dependencies.
    """
    conversation_id = ctx.deps.telegram_id or ctx.deps.user_phone
    if not conversation_id:
        raise ValueError("conversation identifier is required to disable follow-ups")
    if ctx.deps.db_services is None:
        raise ValueError("db_services is required to disable follow-ups")

    await ctx.deps.db_services.ensure_system_message(
        phone=conversation_id,
        message=FOLLOW_UP_OPTOUT_MARKER,
    )
    logger.info(
        "[stop_lead_followups] follow-ups disabled for conversation=%s",
        conversation_id,
    )
    return "Automatic follow-up messages disabled"


async def start_lead_followups(ctx: RunContext[AgentDeps]) -> str:
    """Re-enable lead follow-up messages for this conversation.

    Use this tool only when the user explicitly asks to receive follow-up
    reminders or promotional nudges again after previously opting out.

    Args:
        ctx: Agent run context with dependencies.
    """
    conversation_id = ctx.deps.telegram_id or ctx.deps.user_phone
    if not conversation_id:
        raise ValueError("conversation identifier is required to enable follow-ups")
    if ctx.deps.db_services is None:
        raise ValueError("db_services is required to enable follow-ups")

    await ctx.deps.db_services.deactivate_system_message(
        phone=conversation_id,
        message=FOLLOW_UP_OPTOUT_MARKER,
    )
    logger.info(
        "[start_lead_followups] follow-ups re-enabled for conversation=%s",
        conversation_id,
    )
    return "Automatic follow-up messages re-enabled"

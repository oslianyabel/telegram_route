"""Date-resolver tool — wraps the date sub-agent for use inside _cheese_agent.

The cheese agent delegates all relative-date interpretation to an isolated
sub-agent (`date_agent`) so that date logic is decoupled, testable and can
use a different model or prompt without touching the main agent.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from chatbot.ai_agent.date_agent import DateResolution, run_date_agent
from chatbot.ai_agent.dependencies import AgentDeps

logger = logging.getLogger(__name__)


async def resolve_relative_date(
    ctx: RunContext[AgentDeps],
    query: str,
) -> DateResolution:
    """Interpret a relative date expression and return the resolved absolute date.

    Use this tool whenever the user mentions a relative date such as "mañana",
    "dentro de 3 días", "la semana que viene", "el mes que viene", or any similar
    natural-language time reference. The tool delegates to a specialized sub-agent
    and returns a structured result with the ISO 8601 date.

    Args:
        ctx: Agent run context (injected automatically).
        query: Relative date expression in the user's own words,
               e.g. "mañana", "la semana que viene", "dentro de 5 días".

    Returns:
        DateResolution with:
            - date (str): Resolved date in YYYY-MM-DD format.
            - reasoning (str): Brief explanation of how the date was calculated.
    """
    logger.info("[resolve_relative_date] query=%r", query)
    resolution = await run_date_agent(query)
    logger.info(
        "[resolve_relative_date] %r -> %s | %s",
        query,
        resolution.date,
        resolution.reasoning,
    )
    return resolution

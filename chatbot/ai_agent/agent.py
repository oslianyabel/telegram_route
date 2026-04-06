from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.instructions import (
    get_current_itinerary_context,
    get_pending_deposit_context,
    resolve_or_create_contact,
)
from chatbot.ai_agent.models import ERP_BASE_PATH, GoogleModel, ReservationStatus
from chatbot.ai_agent.tools.booking import (
    add_activities_to_route_preview,
    cancel_reservation,
    cancel_route_booking,
    confirm_add_activities_to_route,
    confirm_modification,
    confirm_route_modification,
    create_pending_reservation,
    create_route_reservation,
    get_cancellation_impact,
    get_reservation_status,
    get_reservations_by_phone,
    get_route_booking_status,
    modify_reservation_preview,
    modify_route_booking_preview,
)
from chatbot.ai_agent.tools.catalog import (
    get_availability,
    get_establishment_details,
    get_experience_detail,
    get_route_availability,
    get_route_detail,
    list_establishments,
    list_experiences,
    list_experiences_by_availability,
    list_routes,
)
from chatbot.ai_agent.tools.customer import (
    update_contact,
    upsert_lead,
)
from chatbot.ai_agent.tools.date_resolver import resolve_relative_date
from chatbot.ai_agent.tools.erp_utils import extract_erp_data
from chatbot.ai_agent.tools.notifications import (
    start_lead_followups,
    stop_lead_followups,
)
from chatbot.ai_agent.tools.payments import get_payment_instructions
from chatbot.ai_agent.tools.support import create_complaint, submit_survey

logger = logging.getLogger(__name__)
ERP_TIMEOUT_SECONDS = 15.0

PROMPT_FILE: Path = Path("static/prompt.txt")
FALLBACK_MODEL: str = GoogleModel.Gemini_3_Flash_Preview


def _load_system_prompt() -> str:
    return PROMPT_FILE.read_text(encoding="utf-8")


def _once_per_turn(tool_name: str):
    """Return a `prepare` callback that disables the tool after its first call."""

    async def prepare(
        ctx: RunContext[AgentDeps], tool_def: ToolDefinition
    ) -> ToolDefinition | None:
        if tool_name in ctx.deps.called_tools:
            logger.debug(
                "[once_per_turn] %s already called this turn — disabled", tool_name
            )
            return None
        return tool_def

    return prepare


async def _only_if_completed_reservations(
    ctx: RunContext[AgentDeps], tool_def: ToolDefinition
) -> ToolDefinition | None:
    """Show submit_survey only when the customer has at least one COMPLETED reservation."""
    if ctx.deps.has_completed_reservations is None:
        if not ctx.deps.user_phone:
            ctx.deps.has_completed_reservations = False
        else:
            try:
                response = await ctx.deps.erp_client.post(
                    f"{ERP_BASE_PATH}.ticket_controller.get_reservations_by_phone",
                    json={
                        "phone": ctx.deps.user_phone,
                        "status": ReservationStatus.COMPLETED,
                        "page": 1,
                        "page_size": 1,
                    },
                    timeout=ERP_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = extract_erp_data(response.json())
                ctx.deps.has_completed_reservations = (data.get("total", 0) or 0) > 0
            except Exception:
                logger.debug(
                    "[_only_if_completed_reservations] could not check reservations for %s",
                    ctx.deps.user_phone,
                )
                ctx.deps.has_completed_reservations = False

    if ctx.deps.has_completed_reservations:
        return tool_def

    logger.debug(
        "[submit_survey] hidden — no completed reservations for %s", ctx.deps.user_phone
    )
    return None


AGENT_TOOLS = [
    # Catalog & discovery (list_experiences and list_routes limited to one call per turn)
    Tool(list_experiences, prepare=_once_per_turn("list_experiences")),
    get_experience_detail,
    Tool(list_routes, prepare=_once_per_turn("list_routes")),
    get_route_detail,
    list_establishments,
    get_establishment_details,
    # Availability
    get_availability,
    list_experiences_by_availability,
    get_route_availability,
    # Customer / CRM (contact resolution runs as system_prompt instruction)
    update_contact,
    upsert_lead,
    stop_lead_followups,
    start_lead_followups,
    # Reservations
    create_pending_reservation,
    get_reservation_status,
    get_reservations_by_phone,
    modify_reservation_preview,
    confirm_modification,
    cancel_reservation,
    # Route reservations
    create_route_reservation,
    get_route_booking_status,
    cancel_route_booking,
    modify_route_booking_preview,
    confirm_route_modification,
    add_activities_to_route_preview,
    confirm_add_activities_to_route,
    # Pricing & cancellation policy
    get_cancellation_impact,
    # Payments
    get_payment_instructions,
    # Date resolution sub-agent
    resolve_relative_date,
    # Support & complaints
    create_complaint,
    Tool(submit_survey, prepare=_only_if_completed_reservations),
]


# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_cheese_agent: Agent[AgentDeps, str] | None = None


def reset_cheese_agent() -> None:
    """Descarta el singleton para que la próxima llamada lo recree con el prompt actualizado."""
    global _cheese_agent  # noqa: PLW0603
    _cheese_agent = None
    logger.info("[reset_cheese_agent] Singleton descartado")


def get_cheese_agent() -> Agent[AgentDeps, str]:
    """Return the singleton cheese agent, creating it on first call."""
    global _cheese_agent  # noqa: PLW0603
    if _cheese_agent is None:
        system_prompt = _load_system_prompt()
        _cheese_agent = Agent(
            model=GoogleModel.Gemini_Flash_Latest,
            system_prompt=system_prompt,
            deps_type=AgentDeps,
            tools=AGENT_TOOLS,
            model_settings=ModelSettings(temperature=0),
        )

        @_cheese_agent.instructions
        def reply_in_user_language_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            return (
                "Always reply in the same language as the user's most recent message. "
                "Ignore the language used by this system prompt, tool schemas, tool outputs, "
                "or ERP data. If any tool returns content in a different language, translate "
                "or adapt it before answering. If the user writes in Spanish, use Rioplatense Spanish."
            )

        @_cheese_agent.instructions
        def current_datetime_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            now = datetime.now(tz=timezone.utc).astimezone()
            return (
                f"Current date and time: {now.strftime('%A %d %B %Y, %H:%M')} "
                f"(server timezone: {now.strftime('%Z %z')}). "
                "Use this date to resolve expressions such as tomorrow, next week, next month, "
                "or in N days."
            )

        @_cheese_agent.instructions
        async def resolve_or_create_contact_instruction(
            ctx: RunContext[AgentDeps],
        ) -> str:
            return await resolve_or_create_contact(ctx)

        @_cheese_agent.instructions
        async def itinerary_context_instruction(
            ctx: RunContext[AgentDeps],
        ) -> str:
            return await get_current_itinerary_context(ctx)

        @_cheese_agent.instructions
        async def pending_deposit_context_instruction(
            ctx: RunContext[AgentDeps],
        ) -> str:
            return await get_pending_deposit_context(ctx)

        @_cheese_agent.system_prompt
        async def list_experiences_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            logger.info("[list_experiences_prompt] called")
            exp_list = await list_experiences(ctx)
            return json.dumps([exp.model_dump_json() for exp in exp_list])

        @_cheese_agent.system_prompt
        async def list_routes_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            logger.info("[list_routes_prompt] called")
            route_list = await list_routes(ctx)
            return json.dumps([route.model_dump_json() for route in route_list])

        @_cheese_agent.system_prompt
        async def list_establishments_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            logger.info("[list_establishments_prompt] called")
            establishment_list = await list_establishments(ctx)
            return json.dumps(
                [
                    establishment.model_dump_json()
                    for establishment in establishment_list
                ]
            )

        logger.info("Cheese agent initialized with %d tools", len(AGENT_TOOLS))
    return _cheese_agent

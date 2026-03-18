from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.instructions import resolve_or_create_contact
from chatbot.ai_agent.models import GoogleModel
from chatbot.ai_agent.prompts import SYSTEM_PROMPT
from chatbot.ai_agent.tools.booking import (
    cancel_reservation,
    confirm_modification,
    create_pending_reservation,
    create_route_reservation,
    get_reservation_status,
    get_reservations_by_phone,
    get_route_booking_status,
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
from chatbot.ai_agent.tools.support import create_complaint

logger = logging.getLogger(__name__)
ERP_TIMEOUT_SECONDS = 15.0


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
    # Reservations
    create_pending_reservation,
    get_reservation_status,
    get_reservations_by_phone,
    confirm_modification,
    cancel_reservation,
    # Route reservations
    create_route_reservation,
    get_route_booking_status,
    # Date resolution sub-agent
    resolve_relative_date,
    # Support & complaints
    create_complaint,
]


# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_cheese_agent: Agent[AgentDeps, str] | None = None


def get_cheese_agent() -> Agent[AgentDeps, str]:
    """Return the singleton cheese agent, creating it on first call."""
    global _cheese_agent  # noqa: PLW0603
    if _cheese_agent is None:
        _cheese_agent = Agent(
            model=GoogleModel.Gemini_Flash_Latest,
            system_prompt=SYSTEM_PROMPT,
            deps_type=AgentDeps,
            tools=AGENT_TOOLS,
            model_settings=ModelSettings(temperature=0),
        )

        @_cheese_agent.instructions
        def current_datetime_prompt(
            ctx: RunContext[AgentDeps],
        ) -> str:
            now = datetime.now(tz=timezone.utc).astimezone()
            return (
                f"Fecha y hora actual: {now.strftime('%A %d de %B de %Y, %H:%M')} "
                f"(zona horaria del servidor: {now.strftime('%Z %z')}). "
                "Usa esta fecha para resolver expresiones como mañana, la semana que viene, "
                "el mes que viene, dentro de N días, etc."
            )

        @_cheese_agent.instructions
        async def resolve_or_create_contact_instruction(
            ctx: RunContext[AgentDeps],
        ) -> str:
            return await resolve_or_create_contact(ctx)

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

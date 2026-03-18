"""Specialized sub-agent for interpreting relative date expressions.

Converts natural-language date references (e.g. "mañana", "dentro de 3 días",
"la semana que viene") into absolute ISO 8601 dates.

Convention rules baked into the system prompt:
- "el mes que viene"  -> first day of next month  (YYYY-MM-01)
- "la semana que viene" -> Monday of next week
- "mañana" / "pasado mañana" -> today + 1 / today + 2
- "dentro de N días" -> today + N
- "el próximo <weekday>" -> the nearest future occurrence of that weekday

Usage::

    result = await run_date_agent("dentro de 3 días")
    print(result.date)       # e.g. "2026-03-08"
    print(result.reasoning)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings

from chatbot.ai_agent.models import GoogleModel

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


class DateResolution(BaseModel):
    """Structured response from the date-resolver sub-agent."""

    date: str
    """Resolved ISO 8601 date (YYYY-MM-DD)."""

    reasoning: str
    """Brief explanation of how the relative expression was resolved."""


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_DATE_SYSTEM_PROMPT: str = """\
Eres un intérprete especializado en expresiones de fecha relativas.

Tu única tarea es convertir una expresión de fecha relativa en una fecha absoluta en \
formato ISO 8601 (YYYY-MM-DD). Sigue estas reglas de interpretación:

REGLAS DE INTERPRETACIÓN
1. "mañana"              -> hoy + 1 día
2. "pasado mañana"       -> hoy + 2 días
3. "dentro de N días"    -> hoy + N días
4. "la semana que viene" -> el lunes de la próxima semana
5. "el mes que viene"    -> día 1 del próximo mes
6. "el próximo <día>"    -> el próximo lunes/martes/... a partir de mañana
7. "en N semanas"        -> hoy + N * 7 días
8. "en N meses"          -> el día 1 del mes actual + N meses

FORMATO DE RESPUESTA
- date: la fecha resuelta en formato YYYY-MM-DD (solo la fecha, sin hora)
- reasoning: una explicación breve de cómo llegaste al resultado

Basa todos los cálculos en la fecha actual que se te inyecta en cada solicitud. \
Nunca inventes ni asumas fechas; si la expresión es ambigua, elige la interpretación \
más natural y explícala en reasoning.
"""

# ---------------------------------------------------------------------------
# Lazy singleton
# ---------------------------------------------------------------------------

_date_agent: Agent[None, DateResolution] | None = None


def get_date_agent() -> Agent[None, DateResolution]:
    """Return the singleton date-resolver agent, creating it on first call."""
    global _date_agent  # noqa: PLW0603
    if _date_agent is None:
        # Ensure GoogleProvider can find the API key (pydantic-settings doesn't
        # inject into os.environ automatically, but pydantic_ai reads from there)
        _date_agent = Agent(
            model=GoogleModel.Gemini_3_Flash_Preview,
            system_prompt=_DATE_SYSTEM_PROMPT,
            output_type=DateResolution,
            model_settings=ModelSettings(temperature=0),
        )
        logger.info("Date agent initialized")
    return _date_agent


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


async def run_date_agent(query: str) -> DateResolution:
    """Interpret a relative date expression and return the resolved date.

    Args:
        query: A natural-language relative date expression,
               e.g. "mañana", "dentro de 3 días", "la semana que viene".

    Returns:
        DateResolution with the ISO 8601 date and the reasoning.
    """
    logger.info("[run_date_agent] query=%r", query)

    now = datetime.now(tz=timezone.utc).astimezone()
    date_context = (
        f"Fecha y hora actual: {now.strftime('%A %d de %B de %Y')} "
        f"({now.strftime('%Y-%m-%d')}). "
        f"Día de la semana en inglés: {now.strftime('%A')}.\n\n"
        f"Expresión a interpretar: {query}"
    )

    # Use a fresh httpx client per call to avoid Windows ProactorEventLoop
    # conflicts caused by pydantic-ai's module-level cached async HTTP client.
    # Timeout must be >10 s; Google API rejects deadlines shorter than 10 s.
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        from pydantic_ai.models.google import (
            GoogleModel as PydanticAIGoogleModel,  # noqa: PLC0415
        )

        provider = GoogleProvider(http_client=http_client)
        # Strip "google-gla:" prefix; PydanticAIGoogleModel expects bare name.
        model_name = GoogleModel.Gemini_Flash_Lite_Latest.value.split(":", 1)[-1]
        google_model = PydanticAIGoogleModel(model_name, provider=provider)
        agent: Agent[None, DateResolution] = Agent(
            model=google_model,
            system_prompt=_DATE_SYSTEM_PROMPT,
            output_type=DateResolution,
            model_settings=ModelSettings(temperature=0),
        )
        result = await agent.run(date_context)
        del agent  # release the agent and its internal state from memory
    return result.output

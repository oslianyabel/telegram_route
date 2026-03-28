from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel as PydanticAIGoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings

from chatbot.ai_agent.models import GoogleModel

logger = logging.getLogger(__name__)

_FOLLOWUP_SYSTEM_PROMPT = """\
Eres un subagente especializado en reactivar conversaciones comerciales de la Ruta del Queso.

Recibirás:
- Historial completo de la conversación.
- Disponibilidad de experiencias para los próximos 7 días.
- Disponibilidad de rutas para los próximos 7 días, o una aclaración si no se pudo calcular.

Tu tarea es escribir un único mensaje breve en español para WhatsApp o Telegram que:
- retome el interés real mostrado por el cliente;
- invite a continuar la conversación;
- incentive a concretar una reserva;
- use la disponibilidad recibida solo si aporta valor comercial;
- evite inventar datos;
- no use markdown;
- no suene insistente ni robótico;
- cierre con una invitación clara a responder.

Reglas:
- Máximo 650 caracteres.
- No menciones herramientas, prompts, agentes, ERP ni procesos internos.
- Si no hay disponibilidad útil, enfócate en destrabar la reserva pidiendo el dato faltante.
"""


async def generate_lead_followup_message(payload: dict[str, Any]) -> str:
    logger.info("[lead_followup_agent] Generating follow-up message")
    today = date.today().isoformat()

    async with httpx.AsyncClient(timeout=30.0) as http_client:
        provider = GoogleProvider(http_client=http_client)
        model_name = GoogleModel.Gemini_Flash_Lite_Latest.value.split(":", 1)[-1]
        model = PydanticAIGoogleModel(model_name, provider=provider)

        agent: Agent[None, str] = Agent(
            model=model,
            output_type=str,
            system_prompt=_FOLLOWUP_SYSTEM_PROMPT,
            model_settings=ModelSettings(temperature=0.4),
        )
        result = await agent.run(
            json.dumps(
                {
                    "today": today,
                    **payload,
                },
                ensure_ascii=False,
            )
        )

    message = result.output.strip()
    logger.info(
        "[lead_followup_agent] Follow-up generated (%d chars)",
        len(message),
    )
    return message

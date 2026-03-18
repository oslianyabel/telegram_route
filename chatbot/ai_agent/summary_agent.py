"""Agente especializado en resumir conversaciones a un único mensaje de contexto.

Produce un resumen compacto que preserva toda la información relevante del
cliente y del pedido para que el agente principal pueda continuar la atención
sin pérdida de contexto.
"""

import logging

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel as PydanticGoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings

from chatbot.core.config import config

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM_PROMPT: str = """
Eres un asistente especializado en comprimir conversaciones de WhatsApp entre un
cliente y el chatbot de Apacha (servicio de viandas) en un único mensaje de contexto.

Tu objetivo es producir un resumen estructurado que permita al agente principal
retomar la conversación sin pérdida de información relevante.

El resumen debe incluir (solo si está disponible):
- DATOS DEL CLIENTE: nombre completo, email, dirección, notas relevantes, horario preferido de entrega, si desea entrega a domicilio o retiro en local, alergias o restricciones alimentarias, etc.
- ESTADO DEL FLUJO: en qué etapa de la venta se encuentra el cliente (lead, cotización, pedido confirmado, etc.).
- PEDIDOS CONFIRMADOS: descripción de pedidos ya procesados con fecha, ítems y total.
- PEDIDO EN CURSO: ítems seleccionados, preferencias de entrega, observaciones pendientes.
- OBSERVACIONES: cualquier aspecto importante mencionado por el cliente que influya en la atención futura.

Reglas de formato:
- Escribe en español, en texto plano, sin markdown.
- Sé conciso: elimina saludos, repeticiones y conversación irrelevante.
- Estructura la información en secciones breves separadas por salto de línea.
- Si una sección no tiene datos, omítela completamente.
- Empieza el resumen con: "RESUMEN DE CONVERSACIÓN PREVIA:"
"""

_google_provider = GoogleProvider(api_key=config.GOOGLE_API_KEY)
_summary_model = PydanticGoogleModel(
    "gemini-flash-lite-latest", provider=_google_provider
)

_summary_agent: Agent[None, str] = Agent(
    model=_summary_model,
    output_type=str,
    system_prompt=_SUMMARY_SYSTEM_PROMPT,
    model_settings=ModelSettings(temperature=0),
)


async def summarize_conversation(chat_str: str) -> str:
    """Genera un resumen compacto de la conversación para usar como mensaje de sistema.

    Args:
        chat_str: Conversación completa en formato "rol: contenido" separado por saltos de línea.

    Returns:
        Resumen estructurado listo para insertar como mensaje de sistema.
    """
    logger.info("[summary_agent] Running summarization")
    result = await _summary_agent.run(chat_str)
    summary: str = result.output
    logger.info(f"[summary_agent] Summary generated ({len(summary)} chars)")
    return summary

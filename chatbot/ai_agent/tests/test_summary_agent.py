# uv run pytest -s chatbot/ai_agent/tests/test_summary_agent.py

"""Tests funcionales para summary_agent.

Llaman al modelo real de Gemini con una conversación ficticia y verifican que
la salida estructurada sea correcta y no ocurran excepciones.

Ejecución completa:
    uv run pytest -s chatbot/ai_agent/tests/test_summary_agent.py -v
"""

from __future__ import annotations

import pytest

from chatbot.ai_agent.summary_agent import summarize_conversation

# ---------------------------------------------------------------------------
# anyio: event loop de sesión para reutilizar el cliente HTTP de Google SDK
# ---------------------------------------------------------------------------

pytest_plugins = ("anyio",)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Conversación ficticia
# ---------------------------------------------------------------------------

_FAKE_CONVERSATION: str = """\
cliente: Hola! Quiero reservar una ruta de quesos para el próximo sábado.
agente: ¡Hola! Con gusto te ayudo. ¿Para cuántas personas sería la reserva?
cliente: Seríamos 4 adultos. Mi nombre es Laura Gómez.
agente: Perfecto, Laura. ¿Tienes alguna preferencia de ruta o establecimiento?
cliente: Me interesa la Ruta del Valle. También me gustaría saber el precio.
agente: La Ruta del Valle tiene un costo de $120 por persona, e incluye degustación y maridaje.
cliente: Excelente. Mi email es laura.gomez@email.com y mi dirección es Calle Falsa 123.
agente: Anotado. ¿Deseas confirmar la reserva para el sábado 22 de marzo para 4 personas?
cliente: Sí, confirmo. Alérgica a los lácteos de vaca, solo tolero quesos de cabra.
agente: Perfectamente. Queda registrada tu reserva. ¡Hasta el sábado!
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_summary_agent.py::test_summarize_conversation
@pytest.mark.anyio(loop_scope="session")
async def test_summarize_conversation() -> None:
    """Llama al agente UNA vez y verifica estructura, encabezado y datos clave.

    Se consolidan todas las aserciones en un único test para evitar múltiples
    llamadas consecutivas al modelo que pueden provocar rate-limiting.
    """
    result: str = await summarize_conversation(_FAKE_CONVERSATION)

    print(f"\n--- RESUMEN GENERADO ---\n{result}\n------------------------")

    # Tipo y contenido básico
    assert isinstance(result, str), "El resultado debe ser una cadena"
    assert len(result.strip()) > 0, "El resumen no debe estar vacío"

    # Encabezado requerido por el system prompt
    assert result.strip().startswith("RESUMEN DE CONVERSACIÓN PREVIA:"), (
        f"Se esperaba el encabezado 'RESUMEN DE CONVERSACIÓN PREVIA:', "
        f"se obtuvo: {result[:80]!r}"
    )

    # Datos clave de la conversación ficticia
    result_lower = result.lower()
    assert "laura" in result_lower, "El resumen debe incluir el nombre del cliente"
    assert "laura.gomez@email.com" in result_lower, "El resumen debe incluir el email"
    assert "4" in result, "El resumen debe incluir la cantidad de personas"

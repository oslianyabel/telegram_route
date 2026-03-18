# uv run pytest -s chatbot/ai_agent/tests/test_error_agent.py

"""Tests funcionales para error_agent.

Llaman al modelo real de Gemini con un error ficticio y verifican que la
salida estructurada (ErrorExplanation) sea correcta y no ocurran excepciones.

Ejecución completa:
    uv run pytest -s chatbot/ai_agent/tests/test_error_agent.py -v
"""

from __future__ import annotations

import pytest

from chatbot.ai_agent.error_agent import ErrorExplanation, run_error_agent

# ---------------------------------------------------------------------------
# anyio: event loop de sesión para reutilizar el cliente HTTP de Google SDK
# ---------------------------------------------------------------------------

pytest_plugins = ("anyio",)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Error ficticio
# ---------------------------------------------------------------------------

_FAKE_AVAILABILITY_ERROR: str = """\
Traceback (most recent call last):
  File "/app/chatbot/ai_agent/tools/catalog.py", line 87, in get_availability
    response = await erp_client.get("/availabilities", params={"experience_id": experience_id})
  File "/app/chatbot/erp/client.py", line 42, in get
    response.raise_for_status()
httpx.HTTPStatusError: Client error '404 Not Found' for url
'https://erp-cheese.deepzide.com/availabilities?experience_id=999'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/404
"""

_FAKE_PAYMENT_ERROR: str = """\
Traceback (most recent call last):
  File "/app/chatbot/ai_agent/tools/payments.py", line 55, in process_payment
    result = await stripe_client.charge(amount=total, currency="ARS")
  File "/app/chatbot/payments/stripe.py", line 30, in charge
    raise ConnectionError("No se pudo conectar con el procesador de pagos: timeout")
ConnectionError: No se pudo conectar con el procesador de pagos: timeout
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_error_agent.py::test_error_agent_returns_explanation
@pytest.mark.anyio(loop_scope="session")
async def test_error_agent_returns_explanation() -> None:
    """El agente debe devolver una instancia válida de ErrorExplanation."""
    result: ErrorExplanation = await run_error_agent(_FAKE_AVAILABILITY_ERROR)

    print("\n--- ERROR EXPLANATION ---")
    print(f"  tool_name   : {result.tool_name}")
    print(f"  user_message: {result.user_message}")
    print("------------------------")

    assert isinstance(result, ErrorExplanation), (
        "El resultado debe ser ErrorExplanation"
    )
    assert isinstance(result.tool_name, str), "tool_name debe ser str"
    assert len(result.tool_name.strip()) > 0, "tool_name no debe estar vacío"
    assert isinstance(result.user_message, str), "user_message debe ser str"
    assert len(result.user_message.strip()) > 0, "user_message no debe estar vacío"


# uv run pytest -s chatbot/ai_agent/tests/test_error_agent.py::test_error_agent_identifies_tool
@pytest.mark.anyio(loop_scope="session")
async def test_error_agent_identifies_tool() -> None:
    """El agente debe identificar correctamente la herramienta que falló (get_availability)."""
    result: ErrorExplanation = await run_error_agent(_FAKE_AVAILABILITY_ERROR)

    print("\n--- TOOL IDENTIFICATION ---")
    print(f"  tool_name   : {result.tool_name}")
    print(f"  user_message: {result.user_message}")
    print("--------------------------")

    assert result.tool_name == "get_availability", (
        f"Se esperaba 'get_availability', se obtuvo: {result.tool_name!r}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_error_agent.py::test_error_agent_payment_error
@pytest.mark.anyio(loop_scope="session")
async def test_error_agent_payment_error() -> None:
    """El agente debe manejar errores de herramientas que no están en el catálogo oficial."""
    result: ErrorExplanation = await run_error_agent(_FAKE_PAYMENT_ERROR)

    print("\n--- PAYMENT ERROR EXPLANATION ---")
    print(f"  tool_name   : {result.tool_name}")
    print(f"  user_message: {result.user_message}")
    print("---------------------------------")

    assert isinstance(result, ErrorExplanation), (
        "El resultado debe ser ErrorExplanation"
    )
    assert len(result.tool_name.strip()) > 0, "tool_name no debe estar vacío"
    assert len(result.user_message.strip()) > 0, "user_message no debe estar vacío"
    # Para errores desconocidos, se espera 'unknown' o algún nombre razonable
    assert len(result.tool_name) < 100, "tool_name no debe ser excesivamente largo"

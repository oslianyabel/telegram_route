# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py

"""Tests for the date-resolver sub-agent.

These tests call the real Gemini model and verify that the agent correctly
interprets relative date expressions into absolute ISO 8601 dates.

No ERP connection is needed; the agent is fully self-contained.

Run the full suite:
    uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py -v
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from chatbot.ai_agent.date_agent import DateResolution, run_date_agent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _next_monday(today: date) -> date:
    """Return the Monday of the next calendar week."""
    days_until_monday = (7 - today.weekday()) % 7
    # If today IS Monday, still jump to NEXT Monday
    if days_until_monday == 0:
        days_until_monday = 7
    return today + timedelta(days=days_until_monday)


def _first_of_next_month(today: date) -> date:
    """Return the 1st day of the next calendar month."""
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_manana
@pytest.mark.anyio(loop_scope="session")
async def test_manana() -> None:
    """'mañana' debe resolverse como hoy + 1 día."""
    today = date.today()
    expected = today + timedelta(days=1)

    result: DateResolution = await run_date_agent("mañana")

    print(f"\n  mañana -> {result.date} | {result.reasoning}")
    assert isinstance(result, DateResolution)
    assert result.date == expected.isoformat(), (
        f"Se esperaba {expected.isoformat()}, se obtuvo {result.date}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_pasado_manana
@pytest.mark.anyio(loop_scope="session")
async def test_pasado_manana() -> None:
    """'pasado mañana' debe resolverse como hoy + 2 días."""
    today = date.today()
    expected = today + timedelta(days=2)

    result: DateResolution = await run_date_agent("pasado mañana")

    print(f"\n  pasado mañana -> {result.date} | {result.reasoning}")
    assert result.date == expected.isoformat(), (
        f"Se esperaba {expected.isoformat()}, se obtuvo {result.date}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_dentro_de_3_dias
@pytest.mark.anyio(loop_scope="session")
async def test_dentro_de_3_dias() -> None:
    """'dentro de 3 días' debe resolverse como hoy + 3 días."""
    today = date.today()
    expected = today + timedelta(days=3)

    result: DateResolution = await run_date_agent("dentro de 3 días")

    print(f"\n  dentro de 3 días -> {result.date} | {result.reasoning}")
    assert result.date == expected.isoformat(), (
        f"Se esperaba {expected.isoformat()}, se obtuvo {result.date}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_dentro_de_7_dias
@pytest.mark.anyio(loop_scope="session")
async def test_dentro_de_7_dias() -> None:
    """'dentro de 7 días' debe resolverse como hoy + 7 días."""
    today = date.today()
    expected = today + timedelta(days=7)

    result: DateResolution = await run_date_agent("dentro de 7 días")

    print(f"\n  dentro de 7 días -> {result.date} | {result.reasoning}")
    assert result.date == expected.isoformat(), (
        f"Se esperaba {expected.isoformat()}, se obtuvo {result.date}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_semana_que_viene
@pytest.mark.anyio(loop_scope="session")
async def test_semana_que_viene() -> None:
    """'la semana que viene' debe resolverse como el lunes de la próxima semana."""
    today = date.today()
    expected = _next_monday(today)

    result: DateResolution = await run_date_agent("la semana que viene")

    print(f"\n  la semana que viene -> {result.date} | {result.reasoning}")
    assert result.date == expected.isoformat(), (
        f"Se esperaba el lunes {expected.isoformat()}, se obtuvo {result.date}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_mes_que_viene
@pytest.mark.anyio(loop_scope="session")
async def test_mes_que_viene() -> None:
    """'el mes que viene' debe resolverse como el día 1 del próximo mes."""
    today = date.today()
    expected = _first_of_next_month(today)

    result: DateResolution = await run_date_agent("el mes que viene")

    print(f"\n  el mes que viene -> {result.date} | {result.reasoning}")
    assert result.date == expected.isoformat(), (
        f"Se esperaba {expected.isoformat()}, se obtuvo {result.date}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_en_dos_semanas
@pytest.mark.anyio(loop_scope="session")
async def test_en_dos_semanas() -> None:
    """'en 2 semanas' debe resolverse como hoy + 14 días."""
    today = date.today()
    expected = today + timedelta(weeks=2)

    result: DateResolution = await run_date_agent("en 2 semanas")

    print(f"\n  en 2 semanas -> {result.date} | {result.reasoning}")
    assert result.date == expected.isoformat(), (
        f"Se esperaba {expected.isoformat()}, se obtuvo {result.date}"
    )


# uv run pytest -s chatbot/ai_agent/tests/test_date_agent.py::test_output_is_date_resolution
@pytest.mark.anyio(loop_scope="session")
async def test_output_is_date_resolution() -> None:
    """El resultado siempre debe ser una instancia de DateResolution con campos válidos."""
    result: DateResolution = await run_date_agent("mañana")

    assert isinstance(result, DateResolution)
    assert result.date  # no vacío
    assert result.reasoning  # no vacío
    # Verificar que date tiene formato YYYY-MM-DD
    parts = result.date.split("-")
    assert len(parts) == 3, f"Formato de fecha inválido: {result.date}"
    assert len(parts[0]) == 4  # año de 4 dígitos
    assert len(parts[1]) == 2  # mes de 2 dígitos
    assert len(parts[2]) == 2  # día de 2 dígitos

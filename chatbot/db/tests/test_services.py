# uv run pytest -s chatbot/db/tests/test_services.py

"""Integration tests for Services read methods against the real database.

These tests connect to the real database configured in .env.
Phone number reserved for these tests: 123456789

Methods covered:
  - get_user
  - get_recent_messages
  - get_pydantic_ai_history
  - get_messages
  - get_chat
  - get_chat_str
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from chatbot.db.schema import init_db
from chatbot.db.services import Services

TEST_PHONE = "123456789"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db():
    """Conexión real a la base de datos por cada test."""
    database = init_db()
    await database.connect()
    yield database
    await database.disconnect()


@pytest.fixture()
def services(db) -> Services:
    """Instancia de Services apuntando a la base de datos real."""
    return Services(database=db)


@pytest.fixture(autouse=True)
async def clean_test_data(services: Services, db) -> AsyncGenerator[None, None]:
    """Elimina datos del teléfono de prueba antes y después de cada test."""
    await services.reset_chat(TEST_PHONE)
    user = await services.get_user(TEST_PHONE)
    if user:
        from chatbot.db.schema import users_table

        await db.execute(users_table.delete().where(users_table.c.phone == TEST_PHONE))
    yield
    await services.reset_chat(TEST_PHONE)
    user = await services.get_user(TEST_PHONE)
    if user:
        from chatbot.db.schema import users_table

        await db.execute(users_table.delete().where(users_table.c.phone == TEST_PHONE))


# ---------------------------------------------------------------------------
# get_user
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_user_found
@pytest.mark.anyio
async def test_get_user_found(services: Services) -> None:
    """Debe retornar la fila del usuario cuando existe en la base de datos."""
    await services.create_user(TEST_PHONE, name="Test User")

    result = await services.get_user(TEST_PHONE)

    print(f"\n  get_user -> phone={result.phone}, name={result.name}")  # type: ignore[union-attr]
    assert result is not None
    assert result.phone == TEST_PHONE  # type: ignore[union-attr]


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_user_not_found
@pytest.mark.anyio
async def test_get_user_not_found(services: Services) -> None:
    """Debe retornar None cuando el usuario no existe."""
    result = await services.get_user(TEST_PHONE)

    print(f"\n  get_user (not found) -> {result}")
    assert result is None


# ---------------------------------------------------------------------------
# get_recent_messages
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_recent_messages_returns_rows
@pytest.mark.anyio
async def test_get_recent_messages_returns_rows(services: Services) -> None:
    """Debe retornar los mensajes creados dentro del rango de horas."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "user", "Hola")
    await services.create_message(TEST_PHONE, "assistant", "¡Bienvenido!")

    result = await services.get_recent_messages(TEST_PHONE, hours=24)

    print(f"\n  get_recent_messages -> {len(result)} mensajes")
    assert len(result) == 2
    assert result[0].role == "user"
    assert result[1].role == "assistant"


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_recent_messages_empty
@pytest.mark.anyio
async def test_get_recent_messages_empty(services: Services) -> None:
    """Debe retornar lista vacía cuando el usuario no tiene mensajes recientes."""
    await services.create_user(TEST_PHONE)

    result = await services.get_recent_messages(TEST_PHONE, hours=24)

    print(f"\n  get_recent_messages (empty) -> {result}")
    assert result == []


# ---------------------------------------------------------------------------
# get_pydantic_ai_history
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_pydantic_ai_history_user_and_assistant
@pytest.mark.anyio
async def test_get_pydantic_ai_history_user_and_assistant(services: Services) -> None:
    """Debe convertir filas user/assistant en ModelRequest/ModelResponse."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "user", "¿Qué tours tienen?")
    await services.create_message(
        TEST_PHONE, "assistant", "Tenemos varios tours disponibles."
    )

    history = await services.get_pydantic_ai_history(TEST_PHONE, hours=24)

    print(f"\n  get_pydantic_ai_history -> {len(history)} entradas")
    assert len(history) == 2
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[0].parts[0], UserPromptPart)
    assert history[0].parts[0].content == "¿Qué tours tienen?"
    assert isinstance(history[1], ModelResponse)
    assert isinstance(history[1].parts[0], TextPart)
    assert history[1].parts[0].content == "Tenemos varios tours disponibles."


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_pydantic_ai_history_system_role
@pytest.mark.anyio
async def test_get_pydantic_ai_history_system_role(services: Services) -> None:
    """Debe convertir filas system en ModelRequest con SystemPromptPart."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "system", "Eres un asistente de turismo.")

    history = await services.get_pydantic_ai_history(TEST_PHONE, hours=24)

    print(f"\n  get_pydantic_ai_history (system) -> {history}")
    assert len(history) == 1
    assert isinstance(history[0], ModelRequest)
    assert isinstance(history[0].parts[0], SystemPromptPart)
    assert history[0].parts[0].content == "Eres un asistente de turismo."


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_pydantic_ai_history_strips_prefix
@pytest.mark.anyio
async def test_get_pydantic_ai_history_strips_prefix(services: Services) -> None:
    """Debe eliminar el prefijo 'Usuario - ' o 'Bot - ' del contenido."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "user", "Usuario - ¿Cuánto cuesta?")
    await services.create_message(TEST_PHONE, "assistant", "Bot - Depende del tour.")

    history = await services.get_pydantic_ai_history(TEST_PHONE, hours=24)

    print(f"\n  get_pydantic_ai_history (strip prefix) -> {len(history)} entradas")
    assert history[0].parts[0].content == "¿Cuánto cuesta?" # type: ignore
    assert history[1].parts[0].content == "Depende del tour."  # type: ignore[attr-defined]


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_pydantic_ai_history_empty
@pytest.mark.anyio
async def test_get_pydantic_ai_history_empty(services: Services) -> None:
    """Debe retornar lista vacía cuando el usuario no tiene mensajes recientes."""
    await services.create_user(TEST_PHONE)

    history = await services.get_pydantic_ai_history(TEST_PHONE, hours=24)

    print(f"\n  get_pydantic_ai_history (empty) -> {history}")
    assert history == []


# ---------------------------------------------------------------------------
# get_messages
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_messages_returns_all_rows
@pytest.mark.anyio
async def test_get_messages_returns_all_rows(services: Services) -> None:
    """Debe retornar todos los mensajes del usuario sin filtro de tiempo."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "user", "Hola")
    await services.create_message(
        TEST_PHONE, "assistant", "¡Hola! ¿En qué te puedo ayudar?"
    )
    await services.create_message(TEST_PHONE, "user", "Quiero reservar.")

    result = await services.get_messages(TEST_PHONE)

    print(f"\n  get_messages -> {len(result)} mensajes")
    assert len(result) == 3


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_messages_empty
@pytest.mark.anyio
async def test_get_messages_empty(services: Services) -> None:
    """Debe retornar lista vacía cuando el usuario no tiene mensajes."""
    await services.create_user(TEST_PHONE)

    result = await services.get_messages(TEST_PHONE)

    print(f"\n  get_messages (empty) -> {result}")
    assert result == []


# ---------------------------------------------------------------------------
# get_chat
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_chat_returns_dicts
@pytest.mark.anyio
async def test_get_chat_returns_dicts(services: Services) -> None:
    """Debe retornar lista de dicts con role y content."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "user", "¿Tienen tours al campo?")
    await services.create_message(TEST_PHONE, "assistant", "Sí, tenemos varios.")

    result = await services.get_chat(TEST_PHONE)

    print(f"\n  get_chat -> {result}")
    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "¿Tienen tours al campo?"}
    assert result[1] == {"role": "assistant", "content": "Sí, tenemos varios."}


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_chat_includes_tools_used
@pytest.mark.anyio
async def test_get_chat_includes_tools_used(services: Services) -> None:
    """Debe incluir tools_used en el dict cuando el mensaje tiene herramientas."""
    await services.create_user(TEST_PHONE)
    await services.create_message(
        TEST_PHONE,
        "assistant",
        "Aquí tienes el catálogo.",
        tools_used=["get_catalog", "get_pricing"],
    )

    result = await services.get_chat(TEST_PHONE)

    print(f"\n  get_chat (tools_used) -> {result}")
    assert len(result) == 1
    assert result[0]["tools_used"] == ["get_catalog", "get_pricing"]


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_chat_no_tools_used_key
@pytest.mark.anyio
async def test_get_chat_no_tools_used_key(services: Services) -> None:
    """No debe incluir tools_used en el dict cuando el mensaje no tiene herramientas."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "user", "Hola")

    result = await services.get_chat(TEST_PHONE)

    print(f"\n  get_chat (no tools_used) -> {result}")
    assert "tools_used" not in result[0]


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_chat_empty
@pytest.mark.anyio
async def test_get_chat_empty(services: Services) -> None:
    """Debe retornar lista vacía cuando el usuario no tiene mensajes."""
    await services.create_user(TEST_PHONE)

    result = await services.get_chat(TEST_PHONE)

    print(f"\n  get_chat (empty) -> {result}")
    assert result == []


# ---------------------------------------------------------------------------
# get_chat_str
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_chat_str_is_valid_json
@pytest.mark.anyio
async def test_get_chat_str_is_valid_json(services: Services) -> None:
    """Debe retornar un JSON string válido con los mensajes."""
    await services.create_user(TEST_PHONE)
    await services.create_message(TEST_PHONE, "user", "¿Cuándo pueden?")
    await services.create_message(TEST_PHONE, "assistant", "Este fin de semana.")

    result = await services.get_chat_str(TEST_PHONE)

    print(f"\n  get_chat_str -> {result}")
    parsed = json.loads(result)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["role"] == "user"
    assert parsed[1]["role"] == "assistant"


# uv run pytest -s chatbot/db/tests/test_services.py::test_get_chat_str_empty_returns_empty_array
@pytest.mark.anyio
async def test_get_chat_str_empty_returns_empty_array(services: Services) -> None:
    """Debe retornar '[]' cuando el usuario no tiene mensajes."""
    await services.create_user(TEST_PHONE)

    result = await services.get_chat_str(TEST_PHONE)

    print(f"\n  get_chat_str (empty) -> {result!r}")
    assert result == "[]"

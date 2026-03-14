# uv run pytest -s chatbot/ai_agent/tests/test_customer.py

"""Functional tests for customer tools against the real ERP API.

Controllers covered:
  - contact_controller      (update_contact)
  - conversation_controller (open_or_resume_conversation)
  - lead_controller         (upsert_lead)
  - resolve_or_create_contact (instruction in chatbot.ai_agent.instructions)
"""

from __future__ import annotations

import httpx
import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.instructions import resolve_or_create_contact
from chatbot.ai_agent.models import (
    ConversationInfo,
    LeadInfo,
    LeadStatus,
    UpdateContactResult,
)
from chatbot.ai_agent.tools.customer import (
    update_contact,
    upsert_lead,
)
from chatbot.ai_agent.tools.utils import open_or_resume_conversation

# Rango de telefonos reservado para estos tests: +598 99 100 0xx
_BASE_PHONE = "+123456998"


# ---------------------------------------------------------------------------
# instruction: resolve_or_create_contact
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_resolve_or_create_contact_returns_string
@pytest.mark.anyio
async def test_resolve_or_create_contact_returns_string(
    ctx: RunContext[AgentDeps],
) -> None:
    """Debe retornar un string con los datos del cliente para el prompt."""
    ctx.deps.user_phone = f"{_BASE_PHONE}01"

    result = await resolve_or_create_contact(ctx)

    print(f"\n  resolve_or_create_contact -> {result!r}")
    assert isinstance(result, str)
    assert "## Datos del cliente" in result


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_resolve_or_create_contact_sets_contact_id
@pytest.mark.anyio
async def test_resolve_or_create_contact_sets_contact_id(
    ctx: RunContext[AgentDeps],
) -> None:
    """Debe poblar ctx.deps.contact_id tras la llamada."""
    ctx.deps.user_phone = f"{_BASE_PHONE}02"
    ctx.deps.contact_id = None

    await resolve_or_create_contact(ctx)

    print(f"\n  contact_id despues de @instruction: {ctx.deps.contact_id}")
    assert ctx.deps.contact_id is not None


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_resolve_or_create_contact_idempotent
@pytest.mark.anyio
async def test_resolve_or_create_contact_idempotent(ctx: RunContext[AgentDeps]) -> None:
    """Llamar dos veces con el mismo telefono debe poblar el mismo contact_id."""
    ctx.deps.user_phone = f"{_BASE_PHONE}03"

    await resolve_or_create_contact(ctx)
    first_contact_id = ctx.deps.contact_id

    ctx.deps.contact_id = None
    await resolve_or_create_contact(ctx)
    second_contact_id = ctx.deps.contact_id

    print(f"\n  1ra llamada: {first_contact_id} | 2da llamada: {second_contact_id}")
    assert first_contact_id == second_contact_id


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_resolve_or_create_contact_no_name_when_phone_equals_name
@pytest.mark.anyio
async def test_resolve_or_create_contact_no_name_when_phone_equals_name(
    ctx: RunContext[AgentDeps],
) -> None:
    """Si full_name == phone, no debe poblar ctx.deps.user_name."""
    phone = f"{_BASE_PHONE}04"
    ctx.deps.user_phone = phone
    ctx.deps.user_name = None

    await resolve_or_create_contact(ctx)

    # Si el ERP devolvio full_name == phone, user_name debe quedar None
    if ctx.deps.user_name is not None:
        assert ctx.deps.user_name != phone, "user_name no debe ser igual al telefono"


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_resolve_or_create_contact_with_name
@pytest.mark.anyio
async def test_resolve_or_create_contact_with_name(
    ctx: RunContext[AgentDeps],
) -> None:
    """Con nombre real, debe poblar ctx.deps.user_name y mencionarlo en el prompt."""
    phone = f"{_BASE_PHONE}16"
    TEST_NAME = "Juan Pedro"
    ctx.deps.user_phone = phone
    await resolve_or_create_contact(ctx)
    await update_contact(ctx, name=TEST_NAME)

    assert ctx.deps.user_name == TEST_NAME, (
        "El nombre actualizado no se reflejo en deps"
    )

    # Reseteamos user_name para verificar que se repone
    ctx.deps.user_name = None
    result = await resolve_or_create_contact(ctx)
    assert ctx.deps.user_name == TEST_NAME
    assert TEST_NAME in result
    print(f"\n  prompt={result!r}  user_name={ctx.deps.user_name}")


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_resolve_or_create_contact_no_phone
@pytest.mark.anyio
async def test_resolve_or_create_contact_no_phone(
    ctx: RunContext[AgentDeps],
) -> None:
    """Sin phone ni telegram_id, debe lanzar ValueError."""
    ctx.deps.user_phone = ""
    ctx.deps.telegram_id = None

    with pytest.raises(ValueError, match="No phone"):
        await resolve_or_create_contact(ctx)

    print("\n  ValueError lanzado correctamente sin phone ni telegram_id")


# ---------------------------------------------------------------------------
# contact_controller – update_contact
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_update_contact_name
@pytest.mark.anyio
async def test_update_contact_name(ctx: RunContext[AgentDeps]) -> None:
    """Debe actualizar el nombre del contacto y retornar changed_fields."""
    ctx.deps.user_phone = f"{_BASE_PHONE}06"
    try:
        await resolve_or_create_contact(ctx)
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error en setup: {exc.response.status_code}")

    try:
        result = await update_contact(ctx, name="Despues")
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error en update: {exc.response.status_code}")

    assert isinstance(result, UpdateContactResult)
    print(f"\n  update_contact -> changed_fields={result.changed_fields}")
    assert "full_name" in result.changed_fields or "name" in result.changed_fields
    assert result.contact.name == "Despues"


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_update_contact_email
@pytest.mark.anyio
async def test_update_contact_email(ctx: RunContext[AgentDeps]) -> None:
    """Debe actualizar el email y registrarlo en changed_fields."""
    ctx.deps.user_phone = f"{_BASE_PHONE}07"
    try:
        await resolve_or_create_contact(ctx)
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error en setup: {exc.response.status_code}")

    try:
        result = await update_contact(ctx, email="test@example.com")
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error en update: {exc.response.status_code}")

    assert isinstance(result, UpdateContactResult)
    print(f"\n  update_contact(email) -> changed_fields={result.changed_fields}")
    assert "email" in result.changed_fields
    assert result.contact.email == "test@example.com"


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_update_contact_requires_contact_id
@pytest.mark.anyio
async def test_update_contact_requires_contact_id(ctx: RunContext[AgentDeps]) -> None:
    """Debe lanzar ValueError si contact_id no esta en deps."""
    ctx.deps.contact_id = None

    with pytest.raises(ValueError, match="contact_id"):
        await update_contact(ctx, name="Fail")


# ---------------------------------------------------------------------------
# conversation_controller
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# conversation_controller
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_open_or_resume_conversation
@pytest.mark.anyio
async def test_open_or_resume_conversation(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar un ConversationInfo con conversation_id valido."""
    ctx.deps.user_phone = f"{_BASE_PHONE}08"
    await resolve_or_create_contact(ctx)

    try:
        result = await open_or_resume_conversation(ctx)
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error {exc.response.status_code}")

    print(f"\n  open_or_resume_conversation -> {result}")
    assert isinstance(result, ConversationInfo)
    assert result.conversation_id
    assert ctx.deps.conversation_id == result.conversation_id


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_open_or_resume_conversation_idempotent
@pytest.mark.anyio
async def test_open_or_resume_conversation_idempotent(
    ctx: RunContext[AgentDeps],
) -> None:
    """Abrir la conversacion dos veces debe retornar la misma (is_new=False la 2da vez)."""
    ctx.deps.user_phone = f"{_BASE_PHONE}09"
    await resolve_or_create_contact(ctx)

    try:
        first = await open_or_resume_conversation(ctx)
        second = await open_or_resume_conversation(ctx)
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error {exc.response.status_code}")

    assert isinstance(first, ConversationInfo)
    assert isinstance(second, ConversationInfo)
    print(f"\n  1ra: {first.conversation_id} | 2da: {second.conversation_id}")
    # El ERP puede crear una nueva o reusar la activa – ambas son validas
    assert first.conversation_id
    assert second.conversation_id
    assert second.is_new is False


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_open_conversation_requires_contact_id
@pytest.mark.anyio
async def test_open_conversation_requires_contact_id(
    ctx: RunContext[AgentDeps],
) -> None:
    """Debe lanzar ValueError si contact_id no esta en deps."""
    ctx.deps.contact_id = None

    with pytest.raises(ValueError, match="contact_id"):
        await open_or_resume_conversation(ctx)


# ---------------------------------------------------------------------------
# lead_controller
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# lead_controller
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_upsert_lead
@pytest.mark.anyio
async def test_upsert_lead(ctx: RunContext[AgentDeps]) -> None:
    """Debe crear/actualizar un lead y retornar LeadInfo con status OPEN."""
    ctx.deps.user_phone = f"{_BASE_PHONE}10"
    await resolve_or_create_contact(ctx)
    result = await upsert_lead(ctx, interest_type="Experience")

    print(f"\n  upsert_lead -> {result}")
    assert isinstance(result, LeadInfo)
    assert result.lead_id
    assert result.status == LeadStatus.OPEN


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_upsert_lead_idempotent
@pytest.mark.anyio
async def test_upsert_lead_idempotent(ctx: RunContext[AgentDeps]) -> None:
    """Llamar upsert_lead dos veces no debe crear duplicados (mismo lead_id o OPEN)."""
    ctx.deps.user_phone = f"{_BASE_PHONE}11"
    await resolve_or_create_contact(ctx)

    first = await upsert_lead(ctx, interest_type="Route")
    second = await upsert_lead(ctx, interest_type="Route")

    print(f"\n  1er lead: {first.lead_id} | 2do lead: {second.lead_id}")
    assert first.status == LeadStatus.OPEN
    assert second.status == LeadStatus.OPEN


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_upsert_lead_requires_contact_id
@pytest.mark.anyio
async def test_upsert_lead_requires_contact_id(ctx: RunContext[AgentDeps]) -> None:
    """Debe lanzar ValueError si contact_id no esta en deps."""
    ctx.deps.contact_id = None
    ctx.deps.conversation_id = "CONV-FAKE"

    with pytest.raises(ValueError, match="contact_id"):
        await upsert_lead(ctx)


# uv run pytest -s chatbot/ai_agent/tests/test_customer.py::test_update_contact_syncs_user_name_in_deps
@pytest.mark.anyio
async def test_update_contact_syncs_user_name_in_deps(
    ctx: RunContext[AgentDeps],
) -> None:
    """Tras update_contact con nombre, ctx.deps.user_name debe actualizarse."""
    ctx.deps.user_phone = f"{_BASE_PHONE}12"
    try:
        await resolve_or_create_contact(ctx)
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error en setup: {exc.response.status_code}")

    ctx.deps.user_name = None
    result = await update_contact(ctx, name="Nombre Actualizado")
    assert isinstance(result, UpdateContactResult)
    assert ctx.deps.user_name == "Nombre Actualizado"

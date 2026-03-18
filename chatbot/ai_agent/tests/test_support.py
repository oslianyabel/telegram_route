# uv run pytest -s chatbot/ai_agent/tests/test_support.py

"""Functional tests for support tools against the real ERP API.

Controllers covered:
  - complaint_controller (create_complaint)
"""

from __future__ import annotations

import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.instructions import resolve_or_create_contact
from chatbot.ai_agent.models import (
    ComplaintIncidentType,
    ComplaintResult,
    ComplaintType,
)
from chatbot.ai_agent.tools.support import create_complaint

# Teléfono reservado para estos tests
_TEST_PHONE = "+5351054482"


# ---------------------------------------------------------------------------
# create_complaint
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_support.py::test_create_complaint_service
@pytest.mark.anyio
async def test_create_complaint_service(ctx: RunContext[AgentDeps]) -> None:
    """Debe crear una queja de tipo Service en el ERP y retornar un ComplaintResult."""
    ctx.deps.user_phone = _TEST_PHONE
    await resolve_or_create_contact(ctx)

    print(f"\n  contact_id={ctx.deps.contact_id}")

    result = await create_complaint(
        ctx,
        description="Test: cliente reporta mala experiencia en el servicio de guía.",
        complaint_type=ComplaintType.SERVICE,
        incident_type=ComplaintIncidentType.LOCAL,
    )

    print(f"  complaint_id={result.complaint_id}")
    print(f"  status={result.status}")
    print(f"  incident_type={result.incident_type}")

    assert isinstance(result, ComplaintResult)
    assert result.complaint_id
    assert result.contact_id == ctx.deps.contact_id
    assert result.status == "OPEN"
    assert result.incident_type == ComplaintIncidentType.LOCAL


# uv run pytest -s chatbot/ai_agent/tests/test_support.py::test_create_complaint_chatbot_issue
@pytest.mark.anyio
async def test_create_complaint_chatbot_issue(ctx: RunContext[AgentDeps]) -> None:
    """Debe crear una queja de tipo Other/REMOTE para problemas con el asistente."""
    ctx.deps.user_phone = _TEST_PHONE
    await resolve_or_create_contact(ctx)

    result = await create_complaint(
        ctx,
        description="Test: el asistente virtual respondió con información incorrecta sobre precios.",
        complaint_type=ComplaintType.OTHER,
        incident_type=ComplaintIncidentType.GENERAL,
    )

    print(f"\n  complaint_id={result.complaint_id}")
    print(f"  incident_type={result.incident_type}")

    assert isinstance(result, ComplaintResult)
    assert result.complaint_id
    assert result.incident_type == ComplaintIncidentType.GENERAL

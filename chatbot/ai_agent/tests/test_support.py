# uv run pytest -s chatbot/ai_agent/tests/test_support.py

"""Functional tests for support tools against the real ERP API.

Controllers covered:
  - complaint_controller (create_complaint)
  - survey_controller (submit_survey)
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
    SurveyResult,
)
from chatbot.ai_agent.tools.support import create_complaint, submit_survey

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


# ---------------------------------------------------------------------------
# submit_survey
# ---------------------------------------------------------------------------

# Ticket de reserva completada reservado para estos tests
_TEST_SURVEY_TICKET_ID = "TKT-2026-03-00078"


# uv run pytest -s chatbot/ai_agent/tests/test_support.py::test_submit_survey_with_comment
@pytest.mark.anyio
async def test_submit_survey_with_comment(ctx: RunContext[AgentDeps]) -> None:
    """Debe enviar una encuesta de satisfacción con rating y comentario al ERP."""
    result = await submit_survey(
        ctx,
        ticket_id=_TEST_SURVEY_TICKET_ID,
        rating=5,
        comment="Great experience!",
    )

    print(f"\n  survey_id={result.survey_id}")
    print(f"  ticket_id={result.ticket_id}")
    print(f"  rating={result.rating}")
    print(f"  comment={result.comment}")
    print(f"  support_case_created={result.support_case_created}")

    assert isinstance(result, SurveyResult)
    assert result.survey_id
    assert result.ticket_id == _TEST_SURVEY_TICKET_ID
    assert result.rating == 5
    assert result.comment == "Great experience!"
    assert result.support_case_created is False


# uv run pytest -s chatbot/ai_agent/tests/test_support.py::test_submit_survey_without_comment
@pytest.mark.anyio
async def test_submit_survey_without_comment(ctx: RunContext[AgentDeps]) -> None:
    """Debe enviar una encuesta de satisfacción sin comentario al ERP."""
    result = await submit_survey(
        ctx,
        ticket_id=_TEST_SURVEY_TICKET_ID,
        rating=3,
    )

    print(f"\n  survey_id={result.survey_id}")
    print(f"  rating={result.rating}")
    print(f"  is_new={result.is_new}")

    assert isinstance(result, SurveyResult)
    assert result.survey_id
    assert result.ticket_id == _TEST_SURVEY_TICKET_ID
    assert result.rating == 3

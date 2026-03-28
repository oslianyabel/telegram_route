# uv run pytest -s chatbot/ai_agent/tests/test_lead_followup.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from chatbot.ai_agent.context import WebhookContextManager
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.tests.conftest import build_run_context
from chatbot.ai_agent.tools.notifications import stop_lead_followups
from chatbot.reminders.lead_followup import evaluate_follow_up_eligibility


@dataclass
class FakeMessageRow:
    role: str
    message: str
    created_at: datetime
    tools_used: str | None = None


def _tool_row(tool_names: list[str], created_at: datetime) -> FakeMessageRow:
    return FakeMessageRow(
        role="assistant",
        message="Bot - mensaje",
        created_at=created_at,
        tools_used=json.dumps(tool_names),
    )


def test_followup_is_sent_after_12h_without_reservation() -> None:
    now = datetime.now(tz=UTC)
    rows = [
        FakeMessageRow(
            role="user",
            message="Usuario - Quiero saber mas",
            created_at=now - timedelta(hours=13),
        ),
        _tool_row(["upsert_lead"], now - timedelta(hours=13)),
    ]

    decision = evaluate_follow_up_eligibility(rows, now=now)

    assert decision.should_send is True
    assert decision.reason == "initial_delay_elapsed"


def test_followup_is_not_sent_before_20h_after_previous_reminder() -> None:
    now = datetime.now(tz=UTC)
    rows = [
        FakeMessageRow(
            role="user",
            message="Usuario - Me interesa una ruta",
            created_at=now - timedelta(hours=23),
        ),
        _tool_row(["upsert_lead"], now - timedelta(hours=23)),
        _tool_row(["lead_followup_reminder"], now - timedelta(hours=10)),
    ]

    decision = evaluate_follow_up_eligibility(rows, now=now)

    assert decision.should_send is False
    assert decision.reason == "repeat_delay_not_elapsed"


def test_followup_is_not_sent_when_reservation_was_already_created() -> None:
    now = datetime.now(tz=UTC)
    rows = [
        FakeMessageRow(
            role="user",
            message="Usuario - Reservemos",
            created_at=now - timedelta(hours=15),
        ),
        _tool_row(["upsert_lead"], now - timedelta(hours=15)),
        _tool_row(["create_pending_reservation"], now - timedelta(hours=14)),
    ]

    decision = evaluate_follow_up_eligibility(rows, now=now)

    assert decision.should_send is False
    assert decision.reason == "reservation_already_created"


def test_followup_is_not_sent_after_24h_of_inactivity() -> None:
    now = datetime(2026, 3, 28, 20, 0, tzinfo=UTC)
    rows = [
        FakeMessageRow(
            role="user",
            message="Usuario - Quiero reservar",
            created_at=now - timedelta(hours=25),
        ),
        _tool_row(["upsert_lead"], now - timedelta(hours=25)),
    ]

    decision = evaluate_follow_up_eligibility(rows, now=now)

    assert decision.should_send is False
    assert decision.reason == "inactive_window_expired"


def test_followup_is_not_sent_after_three_previous_followups() -> None:
    now = datetime(2026, 3, 28, 20, 0, tzinfo=UTC)
    rows = [
        FakeMessageRow(
            role="user",
            message="Usuario - Me interesa",
            created_at=now - timedelta(hours=23),
        ),
        _tool_row(["upsert_lead"], now - timedelta(hours=23)),
        _tool_row(["lead_followup_reminder"], now - timedelta(hours=22)),
        _tool_row(["lead_followup_reminder"], now - timedelta(hours=21)),
        _tool_row(["lead_followup_reminder"], now - timedelta(hours=20)),
    ]

    decision = evaluate_follow_up_eligibility(rows, now=now)

    assert decision.should_send is False
    assert decision.reason == "followup_limit_reached"


def test_followup_is_not_sent_after_opt_out_marker() -> None:
    now = datetime(2026, 3, 28, 20, 0, tzinfo=UTC)
    rows = [
        FakeMessageRow(
            role="user",
            message="Usuario - Me interesa",
            created_at=now - timedelta(hours=13),
        ),
        _tool_row(["upsert_lead"], now - timedelta(hours=13)),
        FakeMessageRow(
            role="system",
            message="FOLLOWUP: opt_out",
            created_at=now - timedelta(hours=1),
        ),
    ]

    decision = evaluate_follow_up_eligibility(rows, now=now)

    assert decision.should_send is False
    assert decision.reason == "followup_opted_out"


class FakeDbServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def ensure_system_message(self, phone: str, message: str) -> None:
        self.calls.append((phone, message))


@pytest.mark.anyio
async def test_stop_lead_followups_tool_persists_opt_out() -> None:
    fake_db = FakeDbServices()
    deps = AgentDeps(
        erp_client=None,  # type: ignore[arg-type]
        db_services=fake_db,  # type: ignore[arg-type]
        whatsapp_client=None,  # type: ignore[arg-type]
        webhook_context=WebhookContextManager(),
        user_phone="59812345678",
    )
    ctx = build_run_context(deps)

    result = await stop_lead_followups(ctx)

    assert fake_db.calls == [("59812345678", "FOLLOWUP: opt_out")]
    assert "mensajes automáticos de seguimiento desactivados" in result

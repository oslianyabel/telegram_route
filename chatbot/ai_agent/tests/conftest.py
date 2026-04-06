"""Shared fixtures for ai_agent functional tests against the real ERP API.

These tests hit the real ERP at https://erp-cheese.deepzide.com using the
credentials from .env.  They validate that the bot's tool functions correctly
consume and parse the API responses.

Run the full suite:
    uv run pytest -s chatbot/ai_agent/tests/ -v
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import ERP_BASE_PATH
from chatbot.erp.client import build_erp_client

# ---------------------------------------------------------------------------
# anyio: use a single session-scoped event loop so pydantic-ai's cached async
# HTTP client (Google SDK) doesn't get attached to a closed event loop between
# individual tests.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Force anyio to use asyncio with a session-scoped event loop."""
    return "asyncio"


# ---------------------------------------------------------------------------
# Stub collaborators (not needed for ERP tests)
# ---------------------------------------------------------------------------


class FakeWhatsAppClient:
    """Stub — we don't send real WhatsApp messages in tests."""

    async def send_text(self, to: str, text: str) -> bool:
        return True


# ---------------------------------------------------------------------------
# RunContext builder (same lightweight stand-in used by tool functions)
# ---------------------------------------------------------------------------


def build_run_context(deps: AgentDeps) -> RunContext[AgentDeps]:
    """Create a minimal RunContext for calling tool functions outside the agent loop."""
    ctx: RunContext[AgentDeps] = RunContext[AgentDeps].__new__(RunContext)  # type: ignore
    object.__setattr__(ctx, "deps", deps)
    object.__setattr__(ctx, "retry", 0)
    object.__setattr__(ctx, "tool_name", "test")
    object.__setattr__(ctx, "messages", [])
    return ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def erp_client() -> AsyncGenerator[httpx.AsyncClient]:  # noqa: ARG001
    """Real httpx.AsyncClient with dynamic token auth pointing at the ERP."""
    client = build_erp_client()
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture()
def deps(erp_client: httpx.AsyncClient) -> AgentDeps:
    """AgentDeps wired to the real ERP, with stubs for WhatsApp/DB."""
    return AgentDeps(
        erp_client=erp_client,
        db_services=None,  # type: ignore[arg-type]
        whatsapp_client=FakeWhatsAppClient(),  # type: ignore[arg-type]
        user_phone="+598 99 000 000",
        user_name=None,
        contact_id=None,
        conversation_id=None,
    )


@pytest.fixture()
def ctx(deps: AgentDeps) -> RunContext[AgentDeps]:
    """Ready-to-use RunContext for tool function calls."""
    return build_run_context(deps)


@pytest.fixture()
def ctx_factory(erp_client: httpx.AsyncClient) -> Callable[..., RunContext[AgentDeps]]:
    """Factory to create RunContext with custom deps overrides."""

    def _factory(**overrides: Any) -> RunContext[AgentDeps]:
        base: dict[str, Any] = {
            "erp_client": erp_client,
            "db_services": None,
            "whatsapp_client": FakeWhatsAppClient(),
            "user_phone": "+598 99 000 000",
            "user_name": None,
            "contact_id": None,
            "conversation_id": None,
        }
        base.update(overrides)
        deps = AgentDeps(**base)  # type: ignore[arg-type]
        return build_run_context(deps)

    return _factory


# ---------------------------------------------------------------------------
# Helper – quick raw POST for setup / teardown
# ---------------------------------------------------------------------------


async def raw_erp_post(
    client: httpx.AsyncClient,
    controller_method: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shortcut to call an ERP endpoint and return the 'message' payload."""
    response = await client.post(
        f"{ERP_BASE_PATH}.{controller_method}",
        json=payload or {},
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json().get("message", response.json())

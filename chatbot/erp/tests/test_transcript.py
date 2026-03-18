# uv run pytest -s chatbot/erp/tests/test_transcript.py

"""Integration tests for upload_message_transcript against the real ERP API.

Run all tests in this module:
    uv run pytest -s chatbot/erp/tests/test_transcript.py -v
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest

from chatbot.erp.client import build_erp_client
from chatbot.erp.transcript import upload_message_transcript

# ---------------------------------------------------------------------------
# anyio: session-scoped event loop to avoid closed-loop issues
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture()
async def erp_client() -> AsyncGenerator[httpx.AsyncClient]:
    """Authenticated ERP client pointing at the real ERP."""
    client = build_erp_client()
    try:
        yield client
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upload_transcript_success(erp_client: httpx.AsyncClient) -> None:
    await upload_message_transcript(
        client=erp_client,
        phone_number="+598 99 000 000",
        user_message="Hola, ¿tenéis rutas disponibles para este fin de semana?",
        bot_response="¡Hola! Sí, tenemos varias rutas disponibles. ¿Te interesa alguna zona en particular?",
    )

# uv run pytest -s chatbot/messaging/tests/test_telegram.py -v
from datetime import datetime

import pytest

from chatbot.messaging.telegram_notifier import (
    MAX_MESSAGE_LENGTH,
    _build_slow_response_message,
    notify_error,
    notify_slow_response,
)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_notify_error() -> None:
    """Envía una notificación de error real al Telegram del desarrollador."""
    exc = ValueError("Error de prueba - test_notify_error")
    await notify_error(exc, context="test_notify_error | phone=+123456789")


@pytest.mark.anyio
async def test_notify_slow_response() -> None:
    """Envía una notificación de respuesta lenta real al Telegram del desarrollador."""
    await notify_slow_response(
        phone="+123456789",
        user_message="¿Qué rutas de queso tienen disponibles para este fin de semana?",
        tools_used=["get_catalog", "get_availability", "get_pricing"],
        ai_response="Tenemos disponibles las siguientes rutas: Ruta del Queso Manchego (sábado 10:00h) y Ruta Artesanal Serrana (domingo 11:00h). El precio por persona es de 45€.",
        message_datetime=datetime.now(),
        history_count=8,
        response_time=80.5,
        provider_error="Timeout en get_availability",
    )


# uv run pytest -s chatbot/messaging/tests/test_telegram.py -k build_slow_response_message
def test_build_slow_response_message_truncates_to_telegram_limit() -> None:
    message = _build_slow_response_message(
        phone="+123456789",
        user_message="consulta " * 120,
        tools_used=["catalog", "availability"],
        ai_response="respuesta " * 1200,
        message_datetime=datetime(2026, 4, 2, 10, 30, 0),
        history_count=12,
        response_time=91.4,
        provider_error="Provider timeout",
    )

    assert len(message) <= MAX_MESSAGE_LENGTH
    assert "Provider timeout" in message
    assert "+123456789" in message

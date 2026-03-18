# uv run pytest -s chatbot/messaging/tests/test_telegram.py -v
from datetime import datetime

import pytest

from chatbot.messaging.telegram_notifier import notify_error, notify_slow_response


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

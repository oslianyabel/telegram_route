"""Send Telegram notifications to the developer when critical errors occur.

Uses the Telegram Bot API directly via httpx (no extra dependency needed).
Configure TELEGRAM_BOT_TOKEN and TELEGRAM_DEV_CHAT_ID in the .env file.
"""
from __future__ import annotations

import logging
import traceback
from datetime import datetime

import httpx

from chatbot.core.config import config

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_LENGTH = 4096
TELEGRAM_API_BASE = "https://api.telegram.org"
_SEND_TIMEOUT = 10.0


async def notify_error(
    exc: BaseException,
    context: str = "",
) -> None:
    """Send an error notification to the developer's Telegram chat.

    Silently logs and returns if the notification fails — never raises,
    so it cannot disrupt the main request flow.

    Args:
        exc: The exception that triggered the notification.
        context: Optional free-text with extra context (e.g. user phone, action).
    """
    token: str = config.TELEGRAM_BOT_TOKEN
    chat_id: str = config.TELEGRAM_DEV_CHAT_ID

    if not token or not chat_id:
        logger.warning(
            "Telegram notifier not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_DEV_CHAT_ID missing)"
        )
        return

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    # Telegram messages are capped at 4096 chars
    tb_truncated = tb[-3500:] if len(tb) > 3500 else tb

    lines: list[str] = [
        "🚨 *Error en Cheese Bot*",
    ]
    if context:
        lines.append(f"📍 *Contexto:* `{context}`")
    lines.append(f"❌ *Excepción:* `{type(exc).__name__}: {exc}`")
    lines.append(f"```\n{tb_truncated}\n```")

    text = "\n".join(lines)

    try:
        async with httpx.AsyncClient(
            base_url=TELEGRAM_API_BASE, timeout=_SEND_TIMEOUT
        ) as client:
            response = await client.post(
                f"/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
            if not response.is_success:
                logger.warning(
                    "Telegram notification failed: %s %s",
                    response.status_code,
                    response.text[:200],
                )
            else:
                logger.debug("Telegram error notification sent to %s", chat_id)
    except Exception as notify_exc:  # noqa: BLE001
        logger.warning("Could not send Telegram notification: %s", notify_exc)


def _build_slow_response_message(
    phone: str,
    user_message: str,
    tools_used: list[str],
    ai_response: str,
    message_datetime: datetime,
    history_count: int,
    response_time: float,
    provider_error: str | None = None,
) -> str:
    """Construye el mensaje de alerta por respuesta lenta.

    Args:
        phone: Número de teléfono del usuario.
        user_message: Consulta enviada por el usuario.
        tools_used: Herramientas empleadas por el agente.
        ai_response: Respuesta generada por el agente.
        message_datetime: Fecha y hora del mensaje.
        history_count: Cantidad de mensajes en el historial.
        response_time: Tiempo de respuesta en segundos.
        provider_error: Descripción del error del proveedor de IA, si hubo alguno.

    Returns:
        Mensaje formateado para Telegram.
    """
    tools_str = ", ".join(tools_used) if tools_used else "ninguna"
    date_str = message_datetime.strftime("%d/%m/%Y %H:%M:%S")
    error_str = f"`{provider_error}`" if provider_error else "ninguno"
    response_preview = (
        ai_response[:300] + "..." if len(ai_response) > 300 else ai_response
    )

    message = (
        f"⏱️ *Respuesta lenta detectada* ({response_time:.1f}s)\n\n"
        f"*Teléfono:* `{phone}`\n"
        f"*Fecha y hora:* `{date_str}`\n"
        f"*Mensajes en historial:* `{history_count}`\n"
        f"*Herramientas empleadas:* `{tools_str}`\n"
        f"*Error del proveedor de IA:* {error_str}\n\n"
        f"*Consulta del usuario:*\n`{user_message[:400]}`\n\n"
        f"*Respuesta del agente:*\n`{response_preview}`"
    )

    if len(message) > MAX_MESSAGE_LENGTH:
        message = message[: MAX_MESSAGE_LENGTH - 10] + "\n...```"
    return message


async def notify_slow_response(
    phone: str,
    user_message: str,
    tools_used: list[str],
    ai_response: str,
    message_datetime: datetime,
    history_count: int,
    response_time: float,
    provider_error: str | None = None,
) -> None:
    """Envía una alerta al desarrollador cuando una respuesta supera el umbral de tiempo.

    Args:
        phone: Número de teléfono del usuario.
        user_message: Consulta enviada por el usuario.
        tools_used: Herramientas empleadas por el agente.
        ai_response: Respuesta generada por el agente.
        message_datetime: Fecha y hora del mensaje.
        history_count: Cantidad de mensajes en el historial.
        response_time: Tiempo de respuesta en segundos.
        provider_error: Descripción del error del proveedor de IA, si hubo alguno.
    """
    url = TELEGRAM_API_URL.format(token=config.TELEGRAM_BOT_TOKEN)
    message = _build_slow_response_message(
        phone=phone,
        user_message=user_message,
        tools_used=tools_used,
        ai_response=ai_response,
        message_datetime=message_datetime,
        history_count=history_count,
        response_time=response_time,
        provider_error=provider_error,
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": config.TELEGRAM_DEV_CHAT_ID,
                    "text": message,
                    "parse_mode": "Markdown",
                },
            )
            response.raise_for_status()
            logger.info(
                f"Slow response notification sent to developer (phone={phone}, time={response_time:.2f}s)."
            )
    except httpx.HTTPError as http_err:
        logger.error(f"Failed to send slow response Telegram notification: {http_err}")

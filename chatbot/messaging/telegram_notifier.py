"""Send Telegram notifications to the developer when critical errors occur.

Uses the Telegram Bot API directly via httpx (no extra dependency needed).
Configure TELEGRAM_BOT_TOKEN and TELEGRAM_DEV_CHAT_ID in the .env file.
"""

from __future__ import annotations

import logging
import traceback

import httpx

from chatbot.core.config import config

logger = logging.getLogger(__name__)

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

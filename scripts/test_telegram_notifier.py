# uv run python scripts/test_telegram_notifier.py

"""Script para probar manualmente el notificador de Telegram.

Verifica que TELEGRAM_BOT_TOKEN y TELEGRAM_DEV_CHAT_ID esten configurados
correctamente enviando una notificacion de prueba.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from chatbot.core.config import config  # noqa: E402
from chatbot.messaging.telegram_notifier import notify_error  # noqa: E402


async def main() -> None:
    print(
        f"TELEGRAM_BOT_TOKEN : {'✅ configurado' if config.TELEGRAM_BOT_TOKEN else '❌ vacío'}"
    )
    print(f"TELEGRAM_DEV_CHAT_ID: {config.TELEGRAM_DEV_CHAT_ID}")

    if not config.TELEGRAM_BOT_TOKEN:
        print("\n❌ Agrega TELEGRAM_BOT_TOKEN en el .env y vuelve a intentarlo.")
        return

    print("\nEnviando notificación de prueba...")

    try:
        raise ValueError("Este es un error de prueba desde test_telegram_notifier.py")
    except ValueError as exc:
        await notify_error(
            exc,
            context="script test_telegram_notifier | usuario=+598 99 000 000 | msg=Hola prueba",
        )

    print("✅ Notificación enviada. Revisa tu Telegram.")


if __name__ == "__main__":
    asyncio.run(main())

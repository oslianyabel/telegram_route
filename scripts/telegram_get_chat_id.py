# uv run python scripts/telegram_get_chat_id.py

"""Muestra los ultimos mensajes recibidos por el bot para obtener los chat_ids.

Pasos:
  1. Abre Telegram y busca tu bot
  2. Enviale cualquier mensaje (ej: /start)
  3. Ejecuta este script
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from chatbot.core.config import config  # noqa: E402


async def main() -> None:
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN no configurado en .env")
        return

    async with httpx.AsyncClient() as client:
        r = await client.get(f"https://api.telegram.org/bot{token}/getUpdates")
        data = r.json()

    updates: list = data.get("result", [])
    if not updates:
        print("Sin mensajes recibidos.")
        print(
            "➡  Abre Telegram, busca tu bot y envíale /start. Luego vuelve a ejecutar."
        )
        return

    print(f"Ultimos {len(updates)} update(s) recibidos:\n")
    for update in updates:
        msg = update.get("message") or update.get("my_chat_member", {})
        chat = msg.get("chat", {})
        print(
            f"  chat_id  = {chat.get('id')}\n"
            f"  username = @{chat.get('username')}\n"
            f"  nombre   = {chat.get('first_name')} {chat.get('last_name') or ''}\n"
            f"  texto    = {msg.get('text', '—')}\n"
        )


if __name__ == "__main__":
    asyncio.run(main())

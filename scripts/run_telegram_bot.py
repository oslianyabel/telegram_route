# uv run python scripts/run_telegram_bot.py

"""Entry point for the Ruta del Queso Telegram chatbot.

Runs the bot in long-polling mode (no webhook needed).

Usage:
    uv run python scripts/run_telegram_bot.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from chatbot.api.telegram_bot import build_application  # noqa: E402


def main() -> None:
    app = build_application()
    print("🤖 Telegram bot running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

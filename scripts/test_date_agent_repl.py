# uv run python scripts/test_date_agent_repl.py

"""Interactive REPL to test the date-resolver sub-agent from the console.

Enter a relative date expression (e.g. "mañana", "dentro de 3 días",
"la semana que viene") and the agent will return the resolved ISO date.

Commands:
  /exit  — quit the REPL
  /help  — show command list
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from chatbot.ai_agent.date_agent import run_date_agent  # noqa: E402

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
# Show only the date agent logs
logging.getLogger("chatbot.ai_agent.date_agent").setLevel(logging.INFO)

HELP_TEXT = """
Comandos disponibles:
  /exit  — salir del REPL
  /help  — mostrar esta ayuda

Ejemplos de consultas:
  mañana
  pasado mañana
  dentro de 3 días
  la semana que viene
  el mes que viene
  en 2 semanas
  el próximo lunes
"""


async def repl() -> None:
    print("=== Date Agent REPL ===")
    print("Escribe una expresión de fecha relativa y presiona Enter.")
    print("Escribe /help para ver ejemplos o /exit para salir.\n")

    while True:
        try:
            query = input("📅 Consulta: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaliendo...")
            break

        if not query:
            continue

        if query == "/exit":
            print("Saliendo...")
            break

        if query == "/help":
            print(HELP_TEXT)
            continue

        try:
            result = await run_date_agent(query)
            print(f"   Fecha resuelta : {result.date}")
            print(f"   Razonamiento   : {result.reasoning}\n")
        except Exception as exc:  # noqa: BLE001
            print(f"   Error: {exc}\n")


if __name__ == "__main__":
    asyncio.run(repl())

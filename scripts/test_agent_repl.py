# uv run python scripts/test_agent_repl.py

"""Interactive REPL to test the Ruta del Queso AI agent in the console.

Supports multi-turn conversation. Type /exit to quit, /clear to reset history.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic_ai.messages import ModelMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chatbot.ai_agent.agent import get_cheese_agent
from chatbot.ai_agent.context import WebhookContextManager
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import GoogleModel
from chatbot.core.config import config
from chatbot.erp.client import build_erp_client

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("chatbot.agent_repl")

# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
# Show DEBUG messages from the chatbot package
logging.getLogger("chatbot").setLevel(logging.DEBUG)

SEPARATOR = "-" * 60
TEST_PHONE = "+598 99 000 000"


class FakeWhatsAppClient:
    """Minimal stub that prints instead of sending real WhatsApp messages."""

    async def send_text(self, to: str, text: str) -> bool:
        logger.info("[WhatsApp stub] -> %s: %s", to, text[:120])
        return True


def _build_deps(erp_client: httpx.AsyncClient) -> AgentDeps:
    """Build AgentDeps with real ERP client and stubs for the rest."""
    return AgentDeps(
        erp_client=erp_client,
        db_services=None,  # type: ignore[arg-type]
        whatsapp_client=FakeWhatsAppClient(),  # type: ignore[arg-type]
        webhook_context=WebhookContextManager(),
        user_phone=TEST_PHONE,
        user_name="Test User",
    )


async def repl() -> None:
    """Run the interactive agent loop."""
    agent = get_cheese_agent()
    message_history: list[ModelMessage] = []

    async with build_erp_client() as erp_client:
        deps = _build_deps(erp_client)

        print(SEPARATOR)
        print("  Ruta del Queso — Agent REPL")
        print(f"  Modelo: {GoogleModel.Gemini_3_Flash_Preview}")
        print(f"  ERP:    {config.ERP_HOST}")
        print("  Escribe /exit para salir, /clear para reiniciar historial")
        print(SEPARATOR)

        while True:
            try:
                user_input: str = input("\n🧀 Tú: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 ¡Hasta luego!")
                break

            if not user_input:
                continue

            if user_input.lower() == "/exit":
                print("👋 ¡Hasta luego!")
                break

            if user_input.lower() == "/clear":
                message_history.clear()
                print("🗑️  Historial limpiado.")
                continue

            try:
                result = await agent.run(
                    user_input,
                    deps=deps,
                    message_history=message_history,
                )

                # Update in-memory history with the full conversation
                message_history = list(result.all_messages())

                print(f"\n🤖 Agente: {result.output}")

                # Show tool usage summary
                tool_calls: list[str] = []
                for msg in result.new_messages():
                    for part in getattr(msg, "parts", []):
                        if hasattr(part, "tool_name"):
                            tool_calls.append(part.tool_name)

                if tool_calls:
                    print(f"   🔧 Tools used: {', '.join(tool_calls)}")

            except KeyboardInterrupt:
                print("\n⚠️  Generation interrupted.")
            except Exception as exc:
                logger.exception("Error during agent execution")
                print(f"\n❌ Error: {exc}")


def main() -> None:
    """Entry point."""
    try:
        asyncio.run(repl())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

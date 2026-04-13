"""Sub-agent that translates static bot messages into the customer's language.

Receives the recent conversation history to detect the user's preferred language
and returns the given message translated into that language.
If the language cannot be determined (empty history) the original message is
returned unchanged so the bot remains functional even for brand-new users.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel as PydanticGoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.settings import ModelSettings

from chatbot.core.config import config

logger = logging.getLogger(__name__)

_MAX_HISTORY_MESSAGES: int = 10

_SYSTEM_PROMPT: str = """
You are a translation assistant for a customer service chatbot called "Ruta del Queso".

Your task:
1. Analyse the customer messages provided to detect the customer's preferred language.
2. Translate the "Message to translate" into that language, preserving its meaning,
   tone and formatting.
3. If the conversation history is empty or the language cannot be determined,
   return the message *unchanged* — do NOT translate.
4. Preserve ALL of the following exactly as they appear:
   - Emojis (e.g. ✅ ⚠️ 🧀 🧾 ⏰)
   - Markdown formatting marks (* _ ~)
   - Ticket IDs (e.g. TKT-2026-03-00018)
   - Deposit IDs
   - Phone numbers, dates, amounts, currency codes (e.g. UYU)
   - URLs
5. Return ONLY the translated message — no preamble, no explanation, nothing else.
""".strip()

_google_provider = GoogleProvider(api_key=config.GOOGLE_API_KEY)
_translation_model = PydanticGoogleModel(
    "gemini-flash-lite-latest", provider=_google_provider
)

_translation_agent: Agent[None, str] = Agent(
    model=_translation_model,
    output_type=str,
    system_prompt=_SYSTEM_PROMPT,
    model_settings=ModelSettings(temperature=0),
)


def _extract_history_snippet(
    messages: list[Any],
    max_messages: int = _MAX_HISTORY_MESSAGES,
) -> str:
    """Return a compact language sample built from the last *max_messages* user turns.

    Works with both plain dicts (``{"role": ..., "content": ...}``) and ORM-style
    objects that expose ``.role`` and ``.message`` attributes.
    """
    user_lines: list[str] = []
    for m in messages:
        if isinstance(m, dict):
            role: str | None = m.get("role")
            content: str | None = m.get("content") or m.get("message")
        else:
            role = getattr(m, "role", None)
            content = getattr(m, "message", None)
        if role == "user" and content:
            user_lines.append(content)
    return "\n".join(user_lines[-max_messages:])


async def _run_translation(history_snippet: str, message: str) -> str:
    """Run the translation agent and return the result.

    Falls back to the original message if the agent raises an exception.
    """
    logger.debug(
        "[translation_agent] Translating message (%d chars), history_snippet=%r",
        len(message),
        history_snippet[:80],
    )
    try:
        prompt = (
            f"Customer messages (for language detection):\n{history_snippet}\n\n"
            f"Message to translate:\n{message}"
        )
        result = await _translation_agent.run(prompt)
        translated: str = result.output.strip()
        logger.debug("[translation_agent] Result: %r", translated[:120])
        return translated
    except Exception as exc:
        logger.warning(
            "[translation_agent] Translation failed, returning original: %s", exc
        )
        return message


async def localize_message(conversation_id: str, message: str) -> str:
    """Translate *message* to the customer's language using their conversation history.

    Fetches the history from the database using *conversation_id* (phone or
    Telegram chat_id).  If the history cannot be loaded the original message is
    returned unchanged.

    Args:
        conversation_id: Phone number or Telegram chat_id used as the DB key.
        message: The static message to translate.

    Returns:
        The message translated into the customer's language, or the original
        message if translation is not possible.
    """
    from chatbot.db.services import services  # local import to avoid circular deps

    try:
        chat = await services.get_chat(conversation_id)
        history_snippet = _extract_history_snippet(chat)
    except Exception as exc:
        logger.warning(
            "[translation_agent] Could not load history for %s: %s",
            conversation_id,
            exc,
        )
        history_snippet = ""

    return await _run_translation(history_snippet, message)


async def localize_message_from_messages(messages: list[Any], message: str) -> str:
    """Translate *message* using an already-loaded list of DB message rows.

    Use this variant in code paths that already hold the full messages list
    (e.g. reminder workers) to avoid a redundant database round-trip.

    Args:
        messages: List of DB message rows (ORM objects with ``.role`` /
            ``.message`` attributes, or plain dicts).
        message: The static message to translate.

    Returns:
        The message translated into the customer's language, or the original
        message if translation is not possible.
    """
    history_snippet = _extract_history_snippet(messages)
    return await _run_translation(history_snippet, message)

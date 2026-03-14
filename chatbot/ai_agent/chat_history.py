"""Chat history persistence and conversion to PydanticAI message format.

Loads / saves messages from the database and converts between the DB
representation and the ``list[ModelMessage]`` format that PydanticAI expects.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

if TYPE_CHECKING:
    from chatbot.db.services import Services

logger = logging.getLogger(__name__)


async def load_history(db: Services, phone: str) -> list[ModelMessage]:
    """Rebuild a PydanticAI message list from DB rows.

    Each stored row has ``role`` ("user" | "assistant") and ``message``.
    """
    rows = await db.get_chat(phone)
    messages: list[ModelMessage] = []

    for row in rows:
        role: str = row["role"]
        content: str = row["content"]

        if role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            messages.append(ModelResponse(parts=[TextPart(content=content)]))

    return messages


async def save_user_message(db: Services, phone: str, text: str) -> None:
    """Persist an incoming user message."""
    await db.create_message(phone=phone, role="user", message=text)


async def save_assistant_message(
    db: Services,
    phone: str,
    text: str,
    tools_used: list[str] | None = None,
) -> None:
    """Persist an outgoing assistant message."""
    await db.create_message(
        phone=phone,
        role="assistant",
        message=text,
        tools_used=tools_used,
    )


async def clear_history(db: Services, phone: str) -> None:
    """Delete all messages for a conversation."""
    await db.reset_chat(phone)

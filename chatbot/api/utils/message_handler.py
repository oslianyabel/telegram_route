import logging

from chatbot.db.services import services

logger = logging.getLogger(__name__)


async def save_user_msg(user_number: str, incoming_msg: str) -> None:
    incoming_msg = f"Usuario - {incoming_msg}"  # noqa: E501
    await services.create_message(
        phone=user_number,
        role="user",
        message=incoming_msg,
    )


async def save_assistant_msg(
    user_number: str, ai_response: str, tools_used: list[str]
) -> None:
    ai_response = f"Bot - {ai_response}"
    await services.create_message(
        phone=user_number,
        role="assistant",
        message=ai_response,
        tools_used=tools_used,
    )

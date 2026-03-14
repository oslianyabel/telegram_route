import logging

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic_ai import AgentRunResult
from pydantic_ai.messages import ModelResponse, ToolCallPart

from chatbot.ai_agent import get_cheese_agent
from chatbot.ai_agent.context import webhook_context_manager
from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.api.utils import message_handler
from chatbot.api.utils.message_queue import Message, message_queue
from chatbot.api.utils.text import strip_markdown
from chatbot.api.utils.webhook_parser import extract_message_content
from chatbot.core.config import config
from chatbot.db.services import services
from chatbot.erp.client import build_erp_client
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.whatsapp import whatsapp_manager

logger = logging.getLogger(__name__)
load_dotenv()
router = APIRouter()
ERROR_STATUS = {"status": "error"}
OK_STATUS = {"status": "ok"}
USER_ERROR_MSG = "Ocurrio un error al procesar tu mensaje. Por favor intentalo de nuevo o escribe /restart para reiniciar el chat."
erp_client: httpx.AsyncClient = build_erp_client()


@router.get("")
async def verify_webhook(request: Request):
    try:
        mode = request.query_params.get("hub.mode")
        challenge = request.query_params.get("hub.challenge")
        token = request.query_params.get("hub.verify_token")

        verify_token_expected = config.WHATSAPP_VERIFY_TOKEN

        if mode == "subscribe" and token == verify_token_expected:
            logger.info("WEBHOOK VERIFIED for Meta WhatsApp API")
            return PlainTextResponse(str(challenge))
        else:
            logger.warning(
                f"Webhook verification failed - Mode: {mode}, "
                f"Token match: {token == verify_token_expected}"
            )
            raise HTTPException(status_code=403, detail="Forbidden")
    except Exception as e:
        logger.error(f"Error in webhook verification: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal Server Error")


def _extract_tools_used(result: AgentRunResult[str]) -> list[str]:
    """Extract tool names called during the agent run."""
    tools: list[str] = []
    for msg in result.all_messages():
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    tools.append(part.tool_name)
    return tools


async def _process_message(message: Message) -> None:
    """Process a single message from the queue sequentially per user."""
    user_number = message.user_number
    incoming_msg = message.content
    message_id = message.message_id

    if not message_id:
        logger.error("No message_id provided for WhatsApp message")
        return

    await whatsapp_manager.mark_read(message_id)
    await whatsapp_manager.send_typing_indicator(message_id)

    try:
        if incoming_msg.lower() == "/restart":
            logger.info("'/restart' requested by %s", user_number)
            await services.reset_chat(user_number)
            await whatsapp_manager.send_text(
                user_number=user_number, text="Chat reiniciado", message_id=message_id
            )
            return

        logger.info("=" * 80)
        logger.info("%s: %s", user_number, incoming_msg)

        await message_handler.save_user_msg(user_number, incoming_msg)

        deps = AgentDeps(
            erp_client=erp_client,
            db_services=services,
            whatsapp_client=whatsapp_manager,
            webhook_context=webhook_context_manager,
            user_phone=user_number,
        )

        agent = get_cheese_agent()
        history = await services.get_pydantic_ai_history(user_number, hours=24)
        result = await agent.run(incoming_msg, deps=deps, message_history=history)

        ai_response: str = strip_markdown(result.output)
        tools_used = _extract_tools_used(result)

        logger.info("🤖 Agent response for %s: %s", user_number, ai_response)
        logger.info("🔧 Tools used: %s", tools_used)

        await message_handler.save_assistant_msg(user_number, ai_response, tools_used)
        await whatsapp_manager.send_text(
            user_number=user_number, text=ai_response, message_id=message_id
        )

    except Exception as exc:
        logger.exception("Error processing message for %s: %s", user_number, exc)
        await notify_error(
            exc,
            context=f"_process_message | user={user_number} | msg={incoming_msg[:200]}",
        )
        await whatsapp_manager.send_text(
            user_number=user_number, text=USER_ERROR_MSG, message_id=message_id
        )


@router.post("")
async def whatsapp_reply(request: Request, background_tasks: BackgroundTasks):
    logger.info("Received WhatsApp message webhook")
    try:
        webhook_data = await request.json()
    except Exception as exc:
        logger.error(f"Error parsing webhook data: {exc}")
        return ERROR_STATUS

    message_data = await extract_message_content(webhook_data)
    if not message_data:
        return OK_STATUS

    user_number, incoming_msg, message_id = message_data

    msg = Message(user_number=user_number, content=incoming_msg, message_id=message_id)
    await message_queue.enqueue(msg)
    await message_queue.start_processing(user_number, _process_message)

    # Notify user if queue is building up
    queue_size = message_queue.queue_size(user_number)
    if queue_size > 1:
        logger.warning(f"Queue size for {user_number} is {queue_size}")

    return OK_STATUS

import logging

from fastapi import APIRouter, Depends

from chatbot.api.utils.security import get_api_key
from chatbot.api.utils.models import Messages, User
from chatbot.db.services import services

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_api_key)])


@router.get("/users", response_model=list[User])
async def get_all_users():
    logger.info("Fetching all users")
    return await services.get_all_users()


@router.get("/users/{phone}", response_model=User)
async def get_user(phone: str):
    logger.info(f"Fetching user with phone: {phone}")
    return await services.get_user(phone)


@router.get("/messages/{phone}", response_model=list[Messages])
async def get_messages(phone: str):
    logger.info(f"Fetching messages for phone: {phone}")
    return await services.get_messages(phone)

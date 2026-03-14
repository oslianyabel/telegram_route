import logging

from fastapi import APIRouter, Depends

from chatbot.api.utils.security import get_api_key

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_api_key)])

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from chatbot.core.config import config

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def get_api_key(api_key: str | None = Security(api_key_header)) -> bool:
    """
    Simple API key dependency
    Compares header `X-API-Key` with `config.ADMIN_API_KEY`

    Raises 401 if missing or invalid.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API Key",
            headers={"WWW-Authenticate": "API-Key"},
        )
    if api_key != config.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API Key",
        )
    return True

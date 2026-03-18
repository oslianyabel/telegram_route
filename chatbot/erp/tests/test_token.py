# uv run pytest -s chatbot/erp/tests/test_token.py::test_fetch_token
import pytest

from chatbot.core.config import config
from chatbot.erp.auth import ERPTokenAuth, ERPTokenData


@pytest.mark.asyncio
async def test_fetch_token() -> None:
    """Obtiene un token real del ERP y verifica que los campos sean válidos."""
    auth = ERPTokenAuth(
        base_url=config.ERP_HOST,
        username=config.ERP_USER,
        password=config.ERP_PASSWORD,
    )

    await auth._fetch_token()

    token: ERPTokenData | None = auth._token_data
    assert token is not None, "No se obtuvo token"
    assert token.api_key, "api_key vacío"
    assert token.api_secret, "api_secret vacío"
    assert token.user, "user vacío"
    assert token.email, "email vacío"
    print(f"\nToken obtenido — user: {token.user} | email: {token.email}")

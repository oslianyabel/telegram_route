"""ERP authentication — fetches and caches a token via the auth endpoint.

The ERP uses Frappe-style token auth: ``Authorization: token <api_key>:<api_secret>``.
Tokens are obtained by posting username/password to the auth endpoint. There is
no explicit expiry in the response, so the token is cached indefinitely and
refreshed transparently on any 401 response (max one retry per request).

Usage::

    auth = ERPTokenAuth(base_url=config.ERP_HOST, username=config.ERP_USER, password=config.ERP_PASSWORD)
    async with httpx.AsyncClient(auth=auth, base_url=config.ERP_HOST) as client:
        response = await client.post(...)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

AUTH_PATH = "/api/method/cheese.api.v1.auth_controller.token"
ERP_TIMEOUT_SECONDS: float = 15.0
AUTH_RETRY_ATTEMPTS: int = 3
AUTH_RETRY_DELAY_SECONDS: float = 1.0
RETRYABLE_AUTH_STATUS_CODES: frozenset[int] = frozenset({502, 503, 504})


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ERPTokenData(BaseModel):
    """Parsed token data returned by the ERP auth endpoint."""

    api_key: str
    api_secret: str
    user: str
    full_name: str
    email: str


# ---------------------------------------------------------------------------
# Auth handler
# ---------------------------------------------------------------------------


class ERPTokenAuth(httpx.Auth):
    """httpx.Auth implementation that fetches and refreshes ERP tokens.

    On every request:
    1. If no token is cached, fetches one first.
    2. Attaches ``Authorization: token <api_key>:<api_secret>`` header.
    3. If the response is 401, refreshes the token once and retries.
    """

    requires_request_body = False
    requires_response_body = False

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._token_data: ERPTokenData | None = None
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request | httpx.Response, Any]:
        """Attach token, yield request, refresh and retry once on 401."""
        await self._ensure_token()
        request.headers["Authorization"] = self._auth_header()
        request.headers.setdefault("Content-Type", "application/json")

        response: httpx.Response = yield request

        if response.status_code == 401:
            logger.warning("[ERPTokenAuth] 401 received — refreshing token")
            await self._refresh_token()
            request.headers["Authorization"] = self._auth_header()
            yield request

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _auth_header(self) -> str:
        assert self._token_data is not None  # noqa: S101
        return f"token {self._token_data.api_key}:{self._token_data.api_secret}"

    async def _ensure_token(self) -> None:
        if self._token_data is None:
            async with self._lock:
                if self._token_data is None:
                    await self._fetch_token()

    async def _refresh_token(self) -> None:
        async with self._lock:
            await self._fetch_token()

    async def _fetch_token(self) -> None:
        """Call the ERP auth endpoint and store the resulting token."""
        logger.info("[ERPTokenAuth] fetching token for user %s", self._username)
        for attempt in range(1, AUTH_RETRY_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=ERP_TIMEOUT_SECONDS) as client:
                    response = await client.post(
                        f"{self._base_url}{AUTH_PATH}",
                        json={
                            "grant_type": "password",
                            "username": self._username,
                            "password": self._password,
                        },
                    )
                    response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                if (
                    status_code not in RETRYABLE_AUTH_STATUS_CODES
                    or attempt == AUTH_RETRY_ATTEMPTS
                ):
                    raise
                logger.warning(
                    "[ERPTokenAuth] token fetch failed with %s on attempt %s/%s; retrying in %.1fs",
                    status_code,
                    attempt,
                    AUTH_RETRY_ATTEMPTS,
                    AUTH_RETRY_DELAY_SECONDS,
                )
            except httpx.RequestError:
                if attempt == AUTH_RETRY_ATTEMPTS:
                    raise
                logger.warning(
                    "[ERPTokenAuth] token fetch request error on attempt %s/%s; retrying in %.1fs",
                    attempt,
                    AUTH_RETRY_ATTEMPTS,
                    AUTH_RETRY_DELAY_SECONDS,
                )

            await asyncio.sleep(AUTH_RETRY_DELAY_SECONDS)

        payload: dict[str, Any] = response.json()
        data = payload["message"]["data"]
        self._token_data = ERPTokenData.model_validate(data)
        logger.info("[ERPTokenAuth] token obtained for user %s", self._token_data.user)

"""Factory for the shared ERP httpx.AsyncClient.

Use ``build_erp_client()`` to obtain a pre-configured client that handles
token-based authentication automatically (including silent refresh on 401).

Example::

    erp_client = build_erp_client()
    # use as a long-lived client; close at app shutdown
    await erp_client.aclose()
"""

from __future__ import annotations

import httpx

from chatbot.core.config import config
from chatbot.erp.auth import ERPTokenAuth

ERP_TIMEOUT_SECONDS: float = 15.0


def build_erp_client(
    base_url: str | None = None,
    timeout: float = ERP_TIMEOUT_SECONDS,
) -> httpx.AsyncClient:
    """Return a configured AsyncClient for the ERP API.

    Args:
        base_url: Override the ERP base URL from config (e.g. for tests).
        timeout: Request timeout in seconds.

    Returns:
        A ready-to-use ``httpx.AsyncClient`` with token auth attached.
    """
    auth = ERPTokenAuth(
        base_url=base_url or config.ERP_HOST,
        username=config.ERP_USER,
        password=config.ERP_PASSWORD,
    )
    return httpx.AsyncClient(
        base_url=base_url or config.ERP_HOST,
        auth=auth,
        timeout=timeout,
    )

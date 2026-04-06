# uv run python scripts/test_user_has_pending_deposit.py

"""Script manual para verificar user_has_pending_deposit con un número de teléfono real.

Imprime el resultado booleano junto al detalle de los tickets CONFIRMED encontrados.
"""

from __future__ import annotations

import asyncio
import logging

from chatbot.ai_agent.tools.payments import user_has_pending_deposit
from chatbot.erp.client import build_erp_client

logging.basicConfig(level=logging.INFO)

USER_PHONE = "1132845402"


async def main() -> None:
    client = build_erp_client()
    try:
        result = await user_has_pending_deposit(
            erp_client=client,
            user_phone=USER_PHONE,
        )
        print(f"\nuser_has_pending_deposit(user_phone={USER_PHONE!r}) → {result}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

"""Quick debug script to inspect ERP response structure."""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatbot.ai_agent.models import ERP_BASE_PATH  # noqa: E402
from chatbot.erp.client import build_erp_client  # noqa: E402


async def debug() -> None:
    async with build_erp_client() as client:
        endpoints = [
            ("list_routes", "route_controller.list_routes", {}),
            (
                "list_establishments",
                "establishment_controller.list_establishments",
                {"page": 1, "page_size": 2},
            ),
            (
                "get_availability",
                "availability_controller.get_availability",
                {
                    "experience_id": "EXP_CREMERIE",
                    "date_from": "-03-2026",
                    "date_to": "31-12-2026",
                },
            ),
        ]

        for label, method, payload in endpoints:
            r = await client.post(f"{ERP_BASE_PATH}.{method}", json=payload)
            raw = r.json()
            wrapper = raw.get("message", raw)
            print(f"=== {label} ===")
            print(f"  status: {r.status_code}")
            if isinstance(wrapper, dict):
                print(f"  wrapper keys: {list(wrapper.keys())}")
                data = wrapper.get("data")
                print(f"  type(data): {type(data)}")
                if isinstance(data, list):
                    print(f"  len(data): {len(data)}")
                    if data:
                        print(
                            f"  first item: {json.dumps(data[0], indent=2, default=str)[:600]}"
                        )
                elif isinstance(data, dict):
                    print(f"  data keys: {list(data.keys())}")
                    print(f"  data: {json.dumps(data, indent=2, default=str)[:600]}")
                meta = wrapper.get("meta")
                if meta:
                    print(f"  meta: {json.dumps(meta, default=str)[:200]}")
            else:
                print(f"  wrapper type: {type(wrapper)}")
                print(f"  wrapper: {str(wrapper)[:500]}")
            print()


asyncio.run(debug())

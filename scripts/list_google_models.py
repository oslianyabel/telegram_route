"""Script to list available Google AI models.

Usage:
    uv run python scripts/list_google_models.py
"""

import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatbot.core.config import config  # noqa: E402

API_KEY = config.GOOGLE_API_KEY
LIST_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"


async def list_models() -> None:
    if not API_KEY:
        print("❌ GOOGLE_API_KEY no está configurada en .env")
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            LIST_MODELS_URL,
            params={"key": API_KEY},
        )
        response.raise_for_status()
        data = response.json()

    models: list[dict] = data.get("models", [])

    # Filter only models that support generateContent
    generate_content_models = [
        m
        for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]

    print(f"\n{'=' * 60}")
    print(f"  Modelos disponibles con generateContent: {len(generate_content_models)}")
    print(f"{'=' * 60}\n")

    for m in generate_content_models:
        name: str = m.get("name", "")
        display: str = m.get("displayName", "")
        description: str = m.get("description", "")[:80]
        input_limit: int = m.get("inputTokenLimit", 0)
        output_limit: int = m.get("outputTokenLimit", 0)

        # PydanticAI model string: strip "models/" prefix and add "google-gla:" prefix
        pydantic_ai_name = f"google-gla:{name.replace('models/', '')}"

        print(f"  📦 {display}")
        print(f"     PydanticAI : {pydantic_ai_name}")
        print(f"     API name   : {name}")
        print(f"     Descripción: {description}...")
        print(f"     Tokens     : input={input_limit:,}  output={output_limit:,}")
        print()


if __name__ == "__main__":
    asyncio.run(list_models())

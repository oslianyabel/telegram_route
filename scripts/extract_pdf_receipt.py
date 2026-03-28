"""Script para extraer datos de un comprobante de pago en PDF y mostrarlos por consola.

Uso:
    uv run python scripts/extract_pdf_receipt.py
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from chatbot.ai_agent.tools.ocr import extract_payment_receipt_from_pdf

PDF_PATH = "static/documents/receipt.pdf"


async def main() -> None:
    print(f"Procesando: {PDF_PATH}\n")
    try:
        receipt = await extract_payment_receipt_from_pdf(PDF_PATH)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        print("Asegúrate de que el archivo static/documents/receipt.pdf exista.")
        sys.exit(1)

    print("=== Comprobante de pago extraído ===")
    print(json.dumps(receipt.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

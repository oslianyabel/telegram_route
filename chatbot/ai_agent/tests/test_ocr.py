# uv run pytest -s chatbot/ai_agent/tests/test_ocr.py

"""Functional test for the OCR vision agent using a real receipt image.

The test hits the real Gemini API with the image at:
    static/images/comprobante.jpeg

Run:
    uv run pytest -s chatbot/ai_agent/tests/test_ocr.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chatbot.ai_agent.models import PaymentReceipt
from chatbot.ai_agent.tools.ocr import extract_payment_receipt

# Ruta absoluta resuelta desde la raíz del proyecto
_RECEIPT_PATH: Path = (
    Path(__file__).parents[3] / "static" / "images" / "comprobante2.png"
)


# ---------------------------------------------------------------------------
# extract_payment_receipt
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_ocr.py::test_extract_payment_receipt
@pytest.mark.anyio
async def test_extract_payment_receipt() -> None:
    """Debe extraer datos estructurados del comprobante de pago con visión IA."""
    assert _RECEIPT_PATH.exists(), (
        f"Imagen de prueba no encontrada: {_RECEIPT_PATH}. "
        "Asegúrate de que static/images/comprobante.jpeg exista en el proyecto."
    )

    print(f"\n  Procesando imagen: {_RECEIPT_PATH}")

    receipt: PaymentReceipt = await extract_payment_receipt(str(_RECEIPT_PATH))

    assert isinstance(receipt, PaymentReceipt)

    print(f"  amount             = {receipt.amount}")
    print(f"  transaction_datetime = {receipt.date}")
    print(f"  reference          = {receipt.reference}")
    print(f"  destination_account= {receipt.account}")
    print(f"  recipient_name     = {receipt.recipient_name}")
    print(f"  payment_method     = {receipt.payment_method}")
    print(f"  branch             = {receipt.branch}")
    print(f"  concept            = {receipt.concept}")

    # Al menos algún campo debe haber sido extraído
    extracted = [
        receipt.amount,
        receipt.date,
        receipt.reference,
        receipt.account,
        receipt.recipient_name,
        receipt.payment_method,
        receipt.branch,
        receipt.concept,
    ]
    assert any(field is not None for field in extracted), (
        "El agente no pudo extraer ningún campo del comprobante."
    )

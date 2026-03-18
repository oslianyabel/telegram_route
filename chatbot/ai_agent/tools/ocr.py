"""OCR agent with vision for payment receipt extraction.

Uses a Gemini vision model to parse JPG/PNG receipt images and return
structured :class:`~chatbot.ai_agent.models.PaymentReceipt` data.

Usage example::

    import asyncio
    from chatbot.ai_agent.tools.ocr import extract_payment_receipt

    receipt = asyncio.run(extract_payment_receipt("path/to/receipt.jpg"))
    print(receipt.amount)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic_ai import Agent, BinaryContent

from chatbot.ai_agent.models import GoogleModel, PaymentReceipt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported media types
# ---------------------------------------------------------------------------

MediaType = Literal["image/jpeg", "image/png"]

_EXTENSION_MAP: dict[str, MediaType] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

_OCR_PROMPT = """\
Analiza la imagen de este comprobante de pago y extrae los siguientes datos \
con la mayor precisión posible.
Devuelve ÚNICAMENTE los campos que puedas leer claramente en la imagen; \
deja en null los que no aparezcan.

Campos requeridos:
- amount: Monto total depositado/pagado (busca "Monto depositado", "Total", "Bs.", etc.)
- date: Fecha y hora de la transacción (formato DD/MM/YYYY HH:MM:SS)
- reference: Número de referencia, barcode o código de transacción
- account: Número de cuenta o información del destinatario
- recipient_name: Nombre de la empresa o persona que recibe el pago
- payment_method: Tipo de pago (Efectivo, Transferencia, Tarjeta, etc.)
- branch: Subagencia, sucursal o ubicación donde se realizó el pago
- concept: Concepto o motivo del pago
"""

# ---------------------------------------------------------------------------
# Lazy singleton agent
# ---------------------------------------------------------------------------

_ocr_agent: Agent[None, PaymentReceipt] | None = None


def _get_ocr_agent() -> Agent[None, PaymentReceipt]:
    """Return the singleton OCR agent, creating it on first call."""
    global _ocr_agent  # noqa: PLW0603
    if _ocr_agent is None:
        _ocr_agent = Agent(
            model=GoogleModel.Gemini_Flash_Latest,
            output_type=PaymentReceipt,
        )
        logger.info("[ocr_agent] OCR agent initialized")
    return _ocr_agent


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract_payment_receipt(image_path: str) -> PaymentReceipt:
    """Extract structured payment data from a receipt image using vision AI.

    Args:
        image_path: Absolute or relative path to the receipt image (JPG or PNG).

    Returns:
        PaymentReceipt with the extracted fields. Fields not found in the
        image will be None.

    Raises:
        ValueError: If the file does not exist or its extension is not supported.
        FileNotFoundError: If the image file cannot be found.
    """
    path = Path(image_path)

    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    suffix = path.suffix.lower()
    media_type = _EXTENSION_MAP.get(suffix)
    if media_type is None:
        raise ValueError(
            f"Unsupported image extension '{suffix}'. "
            f"Supported extensions: {list(_EXTENSION_MAP)}"
        )

    logger.debug(
        "[extract_payment_receipt] processing image: %s (%s)", path, media_type
    )

    image_bytes = path.read_bytes()
    agent = _get_ocr_agent()

    result = await agent.run(
        [
            _OCR_PROMPT,
            BinaryContent(data=image_bytes, media_type=media_type),
        ]
    )

    receipt: PaymentReceipt = result.output
    logger.info(
        "[extract_payment_receipt] extraction complete — amount=%s reference=%s",
        receipt.amount,
        receipt.reference,
    )
    return receipt

"""Tools for payment instructions and deposit management.

Controller: deposit_controller
"""

from __future__ import annotations

import logging
import re

import httpx
from pydantic_ai import ModelRetry, RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    DepositPaymentResult,
    PaymentInstructions,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data, extract_erp_error

logger = logging.getLogger(__name__)

ERP_TIMEOUT_SECONDS = 15.0

# Regex to extract a float amount from a receipt string like "40.00 Bs.", "$40", etc.
_AMOUNT_RE = re.compile(r"[\d]+(?:[.,]\d+)?")


def parse_amount(amount_str: str | None) -> float | None:
    """Parse a raw amount string from OCR into a float.

    Strips currency symbols and locale-specific separators, then casts to float.
    Returns None if parsing fails or the input is None.
    """
    if not amount_str:
        return None
    # Replace comma decimal separator with dot only if there's a single comma
    cleaned = (
        amount_str.replace(",", ".")
        if amount_str.count(",") == 1
        else amount_str.replace(",", "")
    )
    match = _AMOUNT_RE.search(cleaned)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None


async def register_deposit_payment(
    erp_client: httpx.AsyncClient,
    ticket_id: str,
    amount: float,
    ocr_payload: dict | None = None,
) -> DepositPaymentResult:
    """Register a deposit payment in the ERP.

    Calls deposit_controller.record_deposit_payment and returns the result.

    Args:
        erp_client: Authenticated ERP HTTP client.
        ticket_id: ERP ticket identifier (e.g. TKT-2026-03-00018).
        amount: Amount paid, extracted from the receipt.
        ocr_payload: Optional raw OCR data dict to attach to the payment record.

    Returns:
        DepositPaymentResult with the registered payment details.

    Raises:
        httpx.HTTPStatusError: If the ERP returns an HTTP error.
        ValueError: If the ERP returns an unsuccessful response.
    """
    logger.info(
        "[register_deposit_payment] ticket_id=%s amount=%.2f ocr_payload=%s",
        ticket_id,
        amount,
        ocr_payload,
    )

    response = await erp_client.post(
        f"{ERP_BASE_PATH}.deposit_controller.record_deposit_payment",
        json={
            "ticket_id": ticket_id,
            "amount": amount,
            "verification_method": "OCR",
            "ocr_payload": ocr_payload,
        },
        timeout=ERP_TIMEOUT_SECONDS,
    )

    if response.is_error:
        error_msg = extract_erp_error(response.json())
        raise ValueError(
            f"ERP error registering deposit for ticket {ticket_id}: {error_msg}"
        )

    data = extract_erp_data(response.json())
    return DepositPaymentResult.model_validate(data)


async def get_payment_instructions(
    ctx: RunContext[AgentDeps],
    ticket_id: str,
) -> PaymentInstructions:
    """Retrieve payment link and instructions for an individual experience ticket.

    Calls the ERP endpoint deposit_controller.get_payment_link_or_instructions
    and returns the deposit details including the payment link, amounts and
    instructions needed for the user to complete the payment.

    Args:
        ctx: Agent run context with dependencies.
        ticket_id: ERP id of the ticket (e.g. TKT-2026-03-00018).
    """
    logger.info("[get_payment_instructions] ticket_id=%s", ticket_id)

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.deposit_controller.get_payment_link_or_instructions",
        json={"ticket_id": ticket_id},
        timeout=ERP_TIMEOUT_SECONDS,
    )

    if response.is_error:
        error_msg = extract_erp_error(response.json())
        raise ModelRetry(
            f"Error al obtener instrucciones de pago para ticket {ticket_id}: {error_msg}"
        )

    data = extract_erp_data(response.json())
    return PaymentInstructions.model_validate(data)

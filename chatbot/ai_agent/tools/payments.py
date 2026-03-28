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
    ContactInfo,
    CustomerItinerary,
    DepositPaymentResult,
    PaymentInstructions,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data, extract_erp_error

logger = logging.getLogger(__name__)

ERP_TIMEOUT_SECONDS = 15.0

# Regex to extract a float amount from a receipt string like "40.00 Bs.", "$40", etc.
_AMOUNT_RE = re.compile(r"[\d]+(?:[.,]\d+)?")

# ERP VALIDATION_ERROR messages that are caused by business rules (not bugs)
_ERP_VALIDATION_MESSAGES: dict[str, str] = {
    "cannot exceed": "El monto del comprobante supera el monto requerido para el depósito.",
    "PAID deposit": "El depósito ya fue pagado completamente.",
    "already paid": "El depósito ya fue pagado completamente.",
}


def parse_amount(amount_str: str | None) -> float | None:
    """Parse a raw OCR amount string into a float.

    Handles common international formats:
    - European: 1.234,56 or 1.234  (dot=thousands, comma=decimal)
    - US/UK:    1,234.56 or 1,234  (comma=thousands, dot=decimal)
    - Plain:    1234.56 or 1234,56

    Returns None if parsing fails or the input is None.
    """
    if not amount_str:
        return None

    # Strip everything except digits, dots and commas
    s = re.sub(r"[^\d.,]", "", amount_str).strip(".,")
    if not s:
        return None

    has_dot = "." in s
    has_comma = "," in s

    if has_dot and has_comma:
        # Whichever separator appears last is the decimal one
        if s.rindex(".") > s.rindex(","):
            # US format: 1,234.56 → remove commas
            s = s.replace(",", "")
        else:
            # European format: 1.234,56 → remove dots, comma → dot
            s = s.replace(".", "").replace(",", ".")
    elif has_comma:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[-1]) <= 2:  # noqa: PLR2004
            # Decimal comma: 200,50 → 200.50
            s = s.replace(",", ".")
        else:
            # Thousands comma: 27,500 → 27500
            s = s.replace(",", "")
    elif has_dot:
        parts = s.split(".")
        if len(parts) == 2 and len(parts[-1]) <= 2:  # noqa: PLR2004
            # Decimal dot: 200.50 → already correct
            pass
        else:
            # Thousands dot(s): 27.500 or 1.234.567 → remove
            s = s.replace(".", "")

    try:
        return float(s)
    except ValueError:
        return None


def erp_validation_user_message(exc: ValueError) -> str | None:
    """Return a user-friendly message if the error is a known ERP business-rule rejection.

    Returns None when the error is unexpected and should be escalated.
    """
    msg = str(exc)
    for key, user_msg in _ERP_VALIDATION_MESSAGES.items():
        if key in msg:
            return user_msg
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
    # Normalize the `amount` field inside ocr_payload to float.
    # The ERP rejects raw OCR strings like "40.00 Bs.".
    if ocr_payload:
        ocr_payload["amount"] = parse_amount(ocr_payload["amount"])

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
        logger.error(
            "[register_deposit_payment] ERP error response status=%s body=%s",
            response.status_code,
            response.text,
        )
        raise ValueError(
            f"ERP error registering deposit for ticket {ticket_id}: {error_msg}"
        )

    data = extract_erp_data(response.json())
    return DepositPaymentResult.model_validate(data)


async def validate_ticket_ownership(
    erp_client: httpx.AsyncClient,
    user_phone: str,
    ticket_id: str,
) -> None:
    """Validate that a ticket belongs to the user and is in CONFIRMED status.

    Resolves the contact from the ERP using user_phone, retrieves the customer's
    itinerary and checks that the ticket is present and confirmed.

    Args:
        erp_client: Authenticated ERP HTTP client.
        user_phone: User's phone number (used to resolve contact).
        ticket_id: ERP ticket identifier to validate (e.g. TKT-2026-03-00018).

    Raises:
        ValueError: If the ticket does not belong to the user or is not CONFIRMED.
    """
    logger.info(
        "[validate_ticket_ownership] user_phone=%s ticket_id=%s",
        user_phone,
        ticket_id,
    )

    contact_resp = await erp_client.post(
        f"{ERP_BASE_PATH}.contact_controller.resolve_or_create_contact",
        json={"phone": user_phone},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    contact_resp.raise_for_status()
    contact = ContactInfo.model_validate(extract_erp_data(contact_resp.json()))

    itinerary_resp = await erp_client.post(
        f"{ERP_BASE_PATH}.itinerary_controller.get_customer_itinerary",
        json={"contact_id": contact.contact_id},
        timeout=ERP_TIMEOUT_SECONDS,
    )
    itinerary_resp.raise_for_status()
    itinerary = CustomerItinerary.model_validate(extract_erp_data(itinerary_resp.json()))

    for item in itinerary.itinerary:
        for reservation in item.reservations:
            if reservation.reservation_id.upper() == ticket_id.upper():
                if reservation.status.lower() != "confirmed":
                    raise ValueError(
                        f"El ticket {ticket_id} no está en estado CONFIRMADO "
                        f"(estado actual: {reservation.status})."
                    )
                logger.info(
                    "[validate_ticket_ownership] ticket %s validated for user %s",
                    ticket_id,
                    user_phone,
                )
                return

    raise ValueError(
        f"El ticket {ticket_id} no pertenece al número {user_phone}."
    )


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
    result = PaymentInstructions.model_validate(data)
    # Temporarily suppress payment_link — do not expose it to the user.
    result.payment_link = None
    return result

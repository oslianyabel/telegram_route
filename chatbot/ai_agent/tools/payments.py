"""Tools for payment instructions and deposit management.

Controller: deposit_controller
"""

from __future__ import annotations

import json
import logging
import re
from enum import StrEnum
from pathlib import Path

import httpx
from pydantic_ai import ModelRetry, RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    ContactInfo,
    CustomerItinerary,
    DepositPaymentResult,
    EstablishmentDetail,
    ExperienceDetail,
    PaymentInstructions,
    PaymentReceipt,
    ReservationStatusDetail,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data, extract_erp_error

logger = logging.getLogger(__name__)

ERP_TIMEOUT_SECONDS = 15.0

# Regex to extract a float amount from a receipt string like "40.00 Bs.", "$40", etc.
_AMOUNT_RE = re.compile(r"[\d]+(?:[.,]\d+)?")

# ERP VALIDATION_ERROR messages that are caused by business rules (not bugs)
_ERP_VALIDATION_MESSAGES: dict[str, str] = {
    "cannot exceed": "The receipt amount exceeds the amount required for the deposit.",
    "PAID deposit": "The deposit has already been paid in full.",
    "already paid": "The deposit has already been paid in full.",
}

_RECEIPT_CONTENT_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class PendingDepositStatus(StrEnum):
    """Result of checking whether a user has a pending deposit payment."""

    HAS_PENDING = "HAS_PENDING"
    """At least one CONFIRMED ticket with amount_remaining > 0."""

    NO_CONFIRMED_TICKETS = "NO_CONFIRMED_TICKETS"
    """The user has no tickets in CONFIRMED status."""

    NO_PENDING_DEPOSITS = "NO_PENDING_DEPOSITS"
    """The user has CONFIRMED tickets but all deposits are already fully paid."""


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
    receipt_file_path: str | None = None,
) -> DepositPaymentResult:
    """Register a deposit payment in the ERP.

    Calls deposit_controller.record_deposit_payment and returns the result.

    Args:
        erp_client: Authenticated ERP HTTP client.
        ticket_id: ERP ticket identifier (e.g. TKT-2026-03-00018).
        amount: Amount paid, extracted from the receipt.
        ocr_payload: Optional raw OCR data dict to attach to the payment record.
        receipt_file_path: Local path to the downloaded receipt PDF/image.

    Returns:
        DepositPaymentResult with the registered payment details.

    Raises:
        httpx.HTTPStatusError: If the ERP returns an HTTP error.
        ValueError: If the ERP returns an unsuccessful response.
    """
    # Normalize the `amount` field inside ocr_payload to float.
    # The ERP rejects raw OCR strings like "40.00 Bs.".
    if ocr_payload and "amount" in ocr_payload:
        ocr_payload["amount"] = parse_amount(ocr_payload["amount"])

    logger.info(
        "[register_deposit_payment] ticket_id=%s amount=%.2f receipt_file_path=%s ocr_payload=%s",
        ticket_id,
        amount,
        receipt_file_path,
        ocr_payload,
    )

    if receipt_file_path is not None:
        receipt_path = Path(receipt_file_path)
        if not receipt_path.exists():
            raise FileNotFoundError(
                f"Receipt file not found for ticket {ticket_id}: {receipt_file_path}"
            )

        content_type = _RECEIPT_CONTENT_TYPES.get(
            receipt_path.suffix.lower(),
            "application/octet-stream",
        )
        form_data = {
            "ticket_id": ticket_id,
            "amount": str(amount),
            "verification_method": "OCR",
            "ocr_payload": json.dumps(ocr_payload)
            if ocr_payload is not None
            else "null",
            "attach_receipt": "true",
        }
        with receipt_path.open("rb") as receipt_file:
            response = await erp_client.post(
                f"{ERP_BASE_PATH}.deposit_controller.record_deposit_payment",
                data=form_data,
                files={
                    "receipt": (
                        receipt_path.name,
                        receipt_file,
                        content_type,
                    )
                },
                timeout=ERP_TIMEOUT_SECONDS,
            )
    else:
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
    itinerary = CustomerItinerary.model_validate(
        extract_erp_data(itinerary_resp.json())
    )

    for item in itinerary.itinerary:
        for reservation in item.reservations:
            if reservation.reservation_id.upper() == ticket_id.upper():
                if reservation.status.lower() != "confirmed":
                    raise ValueError(
                        f"Ticket {ticket_id} is not in CONFIRMED status "
                        f"(current status: {reservation.status})."
                    )
                logger.info(
                    "[validate_ticket_ownership] ticket %s validated for user %s",
                    ticket_id,
                    user_phone,
                )
                return

    raise ValueError(
        f"Ticket {ticket_id} does not belong to phone number {user_phone}."
    )


async def user_has_pending_deposit(
    erp_client: httpx.AsyncClient,
    user_phone: str,
) -> PendingDepositStatus:
    """Check whether the user has a CONFIRMED ticket with a pending deposit amount.

    Makes the following ERP calls:
    1. contact_controller.resolve_or_create_contact – resolves the contact_id.
    2. itinerary_controller.get_customer_itinerary   – lists all reservations.
    3. deposit_controller.get_deposit_instructions   – called for each CONFIRMED
       ticket until one with amount_remaining > 0 is found (short-circuit).

    Args:
        erp_client: Authenticated ERP HTTP client.
        user_phone: User's phone number used to resolve the contact.

    Returns:
        PendingDepositStatus indicating the specific result.
    """
    logger.info("[user_has_pending_deposit] user_phone=%s", user_phone)

    try:
        contact_resp = await erp_client.post(
            f"{ERP_BASE_PATH}.contact_controller.resolve_or_create_contact",
            json={"phone": user_phone},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        contact_resp.raise_for_status()
        contact = ContactInfo.model_validate(extract_erp_data(contact_resp.json()))
    except Exception as exc:
        logger.warning(
            "[user_has_pending_deposit] Could not resolve contact for %s: %s",
            user_phone,
            exc,
        )
        return PendingDepositStatus.NO_CONFIRMED_TICKETS

    try:
        itinerary_resp = await erp_client.post(
            f"{ERP_BASE_PATH}.itinerary_controller.get_customer_itinerary",
            json={"contact_id": contact.contact_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        itinerary_resp.raise_for_status()
        itinerary = CustomerItinerary.model_validate(
            extract_erp_data(itinerary_resp.json())
        )
    except Exception as exc:
        logger.warning(
            "[user_has_pending_deposit] Could not get itinerary for %s: %s",
            user_phone,
            exc,
        )
        return PendingDepositStatus.NO_CONFIRMED_TICKETS

    confirmed_ticket_ids: list[str] = [
        reservation.reservation_id
        for item in itinerary.itinerary
        for reservation in item.reservations
        if reservation.status.lower() == "confirmed"
    ]

    if not confirmed_ticket_ids:
        logger.info(
            "[user_has_pending_deposit] No CONFIRMED tickets for user=%s", user_phone
        )
        return PendingDepositStatus.NO_CONFIRMED_TICKETS

    for ticket_id in confirmed_ticket_ids:
        try:
            dep_resp = await erp_client.post(
                f"{ERP_BASE_PATH}.deposit_controller.get_deposit_instructions",
                json={"ticket_id": ticket_id},
                timeout=ERP_TIMEOUT_SECONDS,
            )
            if dep_resp.is_error:
                continue
            data = extract_erp_data(dep_resp.json())
            instructions = PaymentInstructions.model_validate(data)
            if (
                instructions.amount_remaining is not None
                and instructions.amount_remaining > 0
            ):
                logger.info(
                    "[user_has_pending_deposit] Found pending deposit for ticket=%s user=%s",
                    ticket_id,
                    user_phone,
                )
                return PendingDepositStatus.HAS_PENDING
        except Exception as exc:
            logger.warning(
                "[user_has_pending_deposit] Error checking deposit for ticket=%s: %s",
                ticket_id,
                exc,
            )
            continue

    logger.info(
        "[user_has_pending_deposit] No pending deposits found for user=%s", user_phone
    )
    return PendingDepositStatus.NO_PENDING_DEPOSITS


async def validate_ocr_against_bank_account(
    erp_client: httpx.AsyncClient,
    ticket_id: str,
    receipt: PaymentReceipt,
) -> tuple[bool, str]:
    """Validate that the OCR receipt data matches the establishment's bank account.

    First checks that bank_name, account and currency were successfully extracted
    from the receipt — if any is missing the validation fails immediately so the
    user is asked to resend a clearer image before any ERP calls are made.

    Then follows the chain:
    1. ticket_controller.get_reservation_status  → experience_id
    2. experience_controller.get_experience_detail → establishment_id
    3. establishment_controller.get_establishment_details → bank_account list

    Compares bank_name, account_number and currency (case-insensitive) from the
    receipt against every entry in the establishment's bank_account list.  A
    field is skipped in the comparison only when the establishment entry itself
    has no value for it.  If the bank_account list is empty the validation is
    skipped and (True, "") is returned so the payment is not blocked.

    Args:
        erp_client: Authenticated ERP HTTP client.
        ticket_id: ERP ticket identifier (e.g. TKT-2026-03-00018).
        receipt: OCR data extracted from the payment receipt.

    Returns:
        A tuple (passed, reason) where ``passed`` is True when validation
        succeeds (or is skipped) and ``reason`` describes the problem when
        ``passed`` is False.
    """
    logger.info("[validate_ocr_against_bank_account] ticket_id=%s", ticket_id)

    # --- 0. Ensure mandatory OCR fields were extracted ---
    missing: list[str] = []
    if not receipt.bank_name:
        missing.append("bank name")
    if not receipt.account:
        missing.append("account number")
    if not receipt.currency:
        missing.append("currency")
    if missing:
        reason = (
            f"The following required fields could not be read from your receipt: "
            f"{', '.join(missing)}. "
            f"Please resend a clearer image where those details are visible."
        )
        logger.warning(
            "[validate_ocr_against_bank_account] Missing OCR fields for ticket=%s: %s",
            ticket_id,
            missing,
        )
        return False, reason

    # --- 1. Get reservation status → experience_id ---
    try:
        ticket_resp = await erp_client.post(
            f"{ERP_BASE_PATH}.ticket_controller.get_reservation_status",
            json={"reservation_id": ticket_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        ticket_resp.raise_for_status()
        ticket_detail = ReservationStatusDetail.model_validate(
            extract_erp_data(ticket_resp.json())
        )
    except Exception as exc:
        logger.warning(
            "[validate_ocr_against_bank_account] Could not get reservation status for ticket=%s: %s",
            ticket_id,
            exc,
        )
        return True, ""

    experience_id: str | None = (
        ticket_detail.experience.experience_id if ticket_detail.experience else None
    )
    if not experience_id:
        logger.warning(
            "[validate_ocr_against_bank_account] No experience_id for ticket=%s — skipping validation",
            ticket_id,
        )
        return True, ""

    # --- 2. Get experience detail → establishment_id ---
    try:
        exp_resp = await erp_client.post(
            f"{ERP_BASE_PATH}.experience_controller.get_experience_detail",
            json={"experience_id": experience_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        exp_resp.raise_for_status()
        experience = ExperienceDetail.model_validate(extract_erp_data(exp_resp.json()))
    except Exception as exc:
        logger.warning(
            "[validate_ocr_against_bank_account] Could not get experience detail for experience=%s: %s",
            experience_id,
            exc,
        )
        return True, ""

    establishment_id: str | None = (
        experience.establishment.id if experience.establishment else None
    )
    if not establishment_id:
        logger.warning(
            "[validate_ocr_against_bank_account] No establishment_id for experience=%s — skipping validation",
            experience_id,
        )
        return True, ""

    # --- 3. Get establishment details → bank_account list ---
    try:
        est_resp = await erp_client.post(
            f"{ERP_BASE_PATH}.establishment_controller.get_establishment_details",
            json={"company_id": establishment_id},
            timeout=ERP_TIMEOUT_SECONDS,
        )
        est_resp.raise_for_status()
        establishment = EstablishmentDetail.model_validate(
            extract_erp_data(est_resp.json())
        )
    except Exception as exc:
        logger.warning(
            "[validate_ocr_against_bank_account] Could not get establishment details for %s: %s",
            establishment_id,
            exc,
        )
        return True, ""

    if not establishment.bank_account:
        logger.info(
            "[validate_ocr_against_bank_account] No bank account configured for establishment=%s — skipping validation",
            establishment_id,
        )
        return True, ""

    # --- 4. Compare OCR fields against each bank account entry ---
    receipt_bank = (receipt.bank_name or "").strip().lower()
    receipt_account = (receipt.account or "").strip()
    receipt_currency = (receipt.currency or "").strip().upper()

    complete_entries: int = 0

    for entry in establishment.bank_account:
        entry_bank = (entry.bank_name or "").strip().lower()
        entry_account = (entry.account_number or "").strip()
        entry_iban = (entry.iban or "").strip()
        entry_currency = (entry.currency or "").strip().upper()

        # Skip entries that are missing any of the required identification fields
        if not entry_bank or not (entry_account or entry_iban) or not entry_currency:
            logger.debug(
                "[validate_ocr_against_bank_account] Skipping incomplete bank_account entry=%r",
                entry.bank_account_id,
            )
            continue

        complete_entries += 1
        bank_match = receipt_bank in entry_bank or entry_bank in receipt_bank
        # receipt `account` may contain account_number or IBAN
        account_match = (
            not entry_account
            or receipt_account == entry_account
            or (entry_iban and receipt_account == entry_iban)
        )
        currency_match = not entry_currency or receipt_currency == entry_currency

        if bank_match and account_match and currency_match:
            logger.info(
                "[validate_ocr_against_bank_account] Match found — bank=%r currency=%r ticket=%s",
                entry.bank_name,
                entry.currency,
                ticket_id,
            )
            return True, ""

    if complete_entries == 0:
        logger.info(
            "[validate_ocr_against_bank_account] All bank_account entries are incomplete for establishment=%s — skipping validation",
            establishment_id,
        )
        return True, ""

    # Build a descriptive mismatch message
    expected_banks = ", ".join(
        f"{e.bank_name} / {e.account_number or e.iban} ({e.currency})"
        for e in establishment.bank_account
        if e.bank_name
    )
    reason = (
        f"The payment receipt details don't match the establishment's bank account. "
        f"Expected: {expected_banks or 'configured bank account'}. "
        f"Got: bank={receipt.bank_name or 'unknown'}, "
        f"account={receipt.account or 'unknown'}, "
        f"currency={receipt.currency or 'unknown'}."
    )
    logger.warning(
        "[validate_ocr_against_bank_account] Mismatch for ticket=%s — %s",
        ticket_id,
        reason,
    )
    return False, reason


async def get_payment_instructions(
    ctx: RunContext[AgentDeps],
    ticket_id: str,
) -> PaymentInstructions:
    """Retrieve deposit payment instructions for an individual experience ticket.

    Calls the ERP endpoint deposit_controller.get_deposit_instructions and
    returns the deposit details needed for the user to complete the payment.
    IMPORTANT: Only call this tool for tickets in CONFIRMED status. Never call
    it for PENDING tickets — payment instructions are sent automatically by the
    system when the establishment confirms the reservation.

    Args:
        ctx: Agent run context with dependencies.
        ticket_id: ERP id of the ticket (e.g. TKT-2026-03-00018).
    """
    logger.info("[get_payment_instructions] ticket_id=%s", ticket_id)

    response = await ctx.deps.erp_client.post(
        f"{ERP_BASE_PATH}.deposit_controller.get_deposit_instructions",
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
    # Preserve the public contract even if the ERP omits payment_link.
    result.payment_link = None
    return result

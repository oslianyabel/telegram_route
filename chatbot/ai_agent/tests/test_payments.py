# uv run pytest -s chatbot/ai_agent/tests/test_payments.py

"""Functional tests for payment instruction tools against the real ERP API.

Controllers covered:
  - deposit_controller (get_payment_link_or_instructions, record_deposit_payment)

Flujo:
  1. Llama a get_payment_instructions con un ticket_id fijo.
  2. Verifica que no lanza ninguna excepción.
  3. Imprime el resultado.
"""

from __future__ import annotations

import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import DepositPaymentResult, PaymentInstructions
from chatbot.ai_agent.tools.payments import (
    get_payment_instructions,
    parse_amount,
    register_deposit_payment,
)

_TEST_TICKET_ID = "TKT-2026-03-00053"

# OCR payload de prueba que simula los datos extraídos de un comprobante real.
# El campo `amount` se envía como string crudo del OCR; register_deposit_payment
# lo convierte a float antes de enviarlo al ERP.
_SAMPLE_OCR_PAYLOAD: dict = {
    "amount": "40.00 Bs.",
    "date": "18/03/2026 10:35:00",
    "reference": "REF-0000123456",
    "account": "0123-4567-89-0123456789",
    "recipient_name": "Ruta del Queso S.A.",
    "payment_method": "Transferencia",
    "branch": "Subagencia Centro",
    "concept": "Depósito de reserva",
}


# ---------------------------------------------------------------------------
# parse_amount — unit tests (sin red)
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_payments.py::test_parse_amount
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Formato europeo: punto=miles, coma=decimal
        ("27.500,00 Bs.", 27500.0),
        ("1.234,56", 1234.56),
        ("27.500", 27500.0),  # miles sin decimal
        # Formato US: coma=miles, punto=decimal
        ("27,500.00", 27500.0),
        ("1,234.56", 1234.56),
        # Coma como decimal (sin separador de miles)
        ("200,00", 200.0),
        ("40,5", 40.5),
        # Punto como decimal
        ("40.00 Bs.", 40.0),
        ("200.50", 200.5),
        # Sin separadores
        ("27500", 27500.0),
        ("200", 200.0),
        # Con símbolos de moneda
        ("$ 40.00", 40.0),
        ("Bs. 200,00", 200.0),
        # Inválidos
        (None, None),
        ("", None),
        ("sin monto", None),
    ],
)
def test_parse_amount(raw: str | None, expected: float | None) -> None:
    """Debe convertir strings OCR de montos a float correctamente."""
    assert parse_amount(raw) == expected


# ---------------------------------------------------------------------------
# get_payment_instructions
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_payments.py::test_get_payment_instructions
@pytest.mark.anyio
async def test_get_payment_instructions(
    ctx: RunContext[AgentDeps],
) -> None:
    """Debe consultar las instrucciones de pago de un ticket fijo sin errores.

    Pasos:
    1. Llama get_payment_instructions con el ticket hardcodeado.
    2. Valida que devuelva un PaymentInstructions correcto e imprime el resultado.
    """
    result: PaymentInstructions = await get_payment_instructions(ctx, _TEST_TICKET_ID)

    assert isinstance(result, PaymentInstructions)
    assert result.ticket_id == _TEST_TICKET_ID
    assert result.deposit_id, "deposit_id no debe estar vacío"

    print(f"\n  deposit_id={result.deposit_id}")
    print(f"  amount_required={result.amount_required}")
    print(f"  amount_paid={result.amount_paid}")
    print(f"  amount_remaining={result.amount_remaining}")
    print(f"  due_at={result.due_at}")
    print(f"  status={result.status}")
    print(f"  payment_link={result.payment_link}")
    print(f"  instructions={result.instructions}")


# ---------------------------------------------------------------------------
# register_deposit_payment
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_payments.py::test_register_deposit_payment
@pytest.mark.anyio
async def test_register_deposit_payment(
    erp_client,
) -> None:
    """Debe registrar un depósito con ocr_payload y devolver DepositPaymentResult.

    Pasos:
    1. Envía un pago de 40.00 Bs. con el ocr_payload de prueba.
    2. Valida que el ERP responda con los campos esperados.
        3. Verifica que amount fue convertido a float antes del envío.
    4. Imprime el resultado completo.
    """
    try:
        result: DepositPaymentResult = await register_deposit_payment(
            erp_client=erp_client,
            ticket_id=_TEST_TICKET_ID,
            amount=40.00,
            ocr_payload=dict(_SAMPLE_OCR_PAYLOAD),  # copia para no mutar el original
        )
    except ValueError as exc:
        if "PAID deposit" in str(exc):
            pytest.skip(
                f"El depósito del ticket {_TEST_TICKET_ID} ya está completamente pagado. "
                "Usa un ticket con pagos pendientes para ejecutar este test."
            )
        raise

    assert isinstance(result, DepositPaymentResult)
    assert result.ticket_id == _TEST_TICKET_ID
    assert result.deposit_id, "deposit_id no debe estar vacío"
    assert result.amount_paid == 40.00
    assert result.amount_remaining >= 0
    assert result.verification_method == "OCR"

    print(f"\n  deposit_id={result.deposit_id}")
    print(f"  ticket_id={result.ticket_id}")
    print(f"  amount_paid={result.amount_paid}")
    print(f"  total_amount_paid={result.total_amount_paid}")
    print(f"  amount_required={result.amount_required}")
    print(f"  amount_remaining={result.amount_remaining}")
    print(f"  old_status={result.old_status}")
    print(f"  new_status={result.new_status}")
    print(f"  verification_method={result.verification_method}")
    print(f"  is_complete={result.is_complete}")

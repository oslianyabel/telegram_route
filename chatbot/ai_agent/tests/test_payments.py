# uv run pytest -s chatbot/ai_agent/tests/test_payments.py

"""Functional tests for payment instruction tools against the real ERP API.

Controllers covered:
  - deposit_controller (get_payment_link_or_instructions)

Flujo:
  1. Llama a get_payment_instructions con un ticket_id fijo.
  2. Verifica que no lanza ninguna excepción.
  3. Imprime el resultado.
"""

from __future__ import annotations

import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import PaymentInstructions
from chatbot.ai_agent.tools.payments import get_payment_instructions

_TEST_TICKET_ID = "TKT-2026-03-00018"


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

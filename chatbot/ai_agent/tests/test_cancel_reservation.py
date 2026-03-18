# uv run pytest -s chatbot/ai_agent/tests/test_cancel_reservation.py

"""Functional tests for cancel_reservation against the real ERP API.

Flujo cubierto:
  1. Usa list_experiences + get_availability para encontrar un experience_id y
     slot_id válidos.
  2. Crea una reserva individual PENDING con create_pending_reservation.
  3. Llama a cancel_reservation con confirmed=False → debe devolver un string
     de confirmación sin tocar el ERP.
  4. Llama a cancel_reservation con confirmed=True → debe cancelar el ticket
     y retornar un CancellationResult con new_status=CANCELLED.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    AvailabilityResponse,
    CancellationResult,
    PendingTicket,
)
from chatbot.ai_agent.tools.booking import (
    cancel_reservation,
    create_pending_reservation,
)
from chatbot.ai_agent.tools.catalog import get_availability, list_experiences

_TEST_CONTACT_ID = "+5351054484"
_TEST_PARTY_SIZE = 1


async def _find_available_slot(
    ctx: RunContext[AgentDeps],
) -> tuple[str, str]:
    """Busca el primer (experience_id, slot_id) con disponibilidad en el ERP."""
    experiences = await list_experiences(ctx)
    if not experiences:
        pytest.skip("El ERP no devolvió ninguna experiencia ONLINE.")

    today = date.today()
    today_iso = today.isoformat()  # YYYY-MM-DD para comparar con slot.date
    date_from = today.strftime("%d-%m-%Y")
    date_to = (today + timedelta(days=180)).strftime("%d-%m-%Y")

    for exp in experiences:
        availability: AvailabilityResponse = await get_availability(
            ctx, exp.experience_id, date_from, date_to
        )
        for slot in availability.slots:
            capacity = slot.available_capacity or 0
            # Ignorar slots en fechas pasadas
            if slot.date and slot.date < today_iso:
                continue
            if slot.is_available and capacity >= _TEST_PARTY_SIZE:
                print(
                    f"  [find_slot] experience={exp.experience_id} "
                    f"slot={slot.slot_id} date={slot.date} capacity={capacity}"
                )
                return exp.experience_id, slot.slot_id

    pytest.skip(
        "No se encontró ningún slot con disponibilidad. "
        "Verifica el ERP o amplía el rango de búsqueda."
    )


# ---------------------------------------------------------------------------
# create_pending_reservation → cancel_reservation (sin confirmar → confirmar)
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_cancel_reservation.py::test_create_and_cancel_reservation
@pytest.mark.anyio
async def test_create_and_cancel_reservation(
    ctx: RunContext[AgentDeps],
    ctx_factory: Callable[..., RunContext[AgentDeps]],
) -> None:
    """Debe crear una reserva PENDING y luego cancelarla con confirmación explícita.

    Pasos:
    1. Descubre un slot disponible con ctx (sin contact_id).
    2. Crea el ticket PENDING con ctx_factory(contact_id=...).
    3. Llama cancel_reservation(confirmed=False) → devuelve string, no cancela.
    4. Llama cancel_reservation(confirmed=True) → cancela y retorna CancellationResult.
    """
    # -- 1. Buscar slot disponible (no necesita contact_id) ------------------
    experience_id, slot_id = await _find_available_slot(ctx)
    print(f"\n  experience_id={experience_id}  slot_id={slot_id}")

    # -- 2. Crear reserva PENDING (requiere contact_id) ----------------------
    booking_ctx = ctx_factory(contact_id=_TEST_CONTACT_ID)

    try:
        ticket: PendingTicket = await create_pending_reservation(
            booking_ctx,
            experience_id=experience_id,
            slot_id=slot_id,
            party_size=_TEST_PARTY_SIZE,
        )
    except Exception as exc:
        # Imprimir el body de la respuesta ERP si está disponible
        response_obj = getattr(exc, "response", None)
        if response_obj is not None:
            print(f"  [ERP error body] {response_obj.text}")
        raise
    print(f"  ticket_id={ticket.ticket_id}  status={ticket.status}")
    print(f"  expires_at={ticket.expires_at}")

    assert isinstance(ticket, PendingTicket)
    assert ticket.ticket_id
    assert ticket.status == "PENDING"

    # -- 3. Intentar cancelar SIN confirmar ----------------------------------
    unconfirmed_response = await cancel_reservation(
        booking_ctx, reservation_id=ticket.ticket_id, confirmed=False
    )
    print(f"\n  [confirmed=False] respuesta: {unconfirmed_response!r}")

    assert isinstance(unconfirmed_response, str), (
        "Con confirmed=False debe devolver un string de confirmación, "
        "no ejecutar la cancelación"
    )
    assert ticket.ticket_id in unconfirmed_response

    # -- 4. Confirmar la cancelación -----------------------------------------
    result = await cancel_reservation(
        booking_ctx, reservation_id=ticket.ticket_id, confirmed=True
    )
    assert isinstance(result, CancellationResult)

    print(f"\n  [confirmed=True] ticket_id={result.ticket_id}")
    print(f"  old_status={result.old_status}  new_status={result.new_status}")
    print(f"  slot_id={result.slot_id}")

    assert result.ticket_id == ticket.ticket_id
    assert result.new_status == "CANCELLED"
    assert result.old_status in {"PENDING", "CONFIRMED"}

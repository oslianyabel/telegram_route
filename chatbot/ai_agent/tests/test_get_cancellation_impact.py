# uv run pytest -s chatbot/ai_agent/tests/test_get_cancellation_impact.py

"""Functional tests for get_cancellation_impact against the real ERP API.

Flujo cubierto:
  1. Crea una reserva PENDING con create_pending_reservation para obtener un
     reservation_id válido.
  2. Llama a get_cancellation_impact con ese reservation_id.
  3. Verifica que la respuesta sea un CancellationImpact sin errores y que los
     campos obligatorios estén presentes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta

import httpx
import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    AvailabilityResponse,
    CancellationImpact,
    PendingTicket,
)
from chatbot.ai_agent.tools.booking import (
    cancel_reservation,
    create_pending_reservation,
    get_cancellation_impact,
)
from chatbot.ai_agent.tools.catalog import get_availability, list_experiences

_TEST_CONTACT_ID = "+5351054484"
_TEST_USER_NAME = "Test Customer"
_TEST_PARTY_SIZE = 1


def _skip_if_erp_unavailable(exc: httpx.HTTPError) -> None:
    """Skip the test when the real ERP is temporarily unavailable."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        pytest.skip(
            f"ERP temporalmente no disponible ({exc.response.status_code}) "
            f"en {exc.request.url}"
        )
    if isinstance(exc, httpx.RequestError):
        pytest.skip(f"No se pudo contactar al ERP: {exc}")


async def _find_available_slot(
    ctx: RunContext[AgentDeps],
) -> tuple[str, str, str]:
    """Busca el primer (experience_id, slot_id, date) con disponibilidad en el ERP."""
    experiences = await list_experiences(ctx)
    if not experiences:
        pytest.skip("El ERP no devolvió ninguna experiencia ONLINE.")

    today = date.today()
    today_iso = today.isoformat()
    date_from = today.strftime("%Y-%m-%d")
    date_to = (today + timedelta(days=180)).strftime("%Y-%m-%d")

    for exp in experiences:
        availability: AvailabilityResponse = await get_availability(
            ctx, exp.experience_id, date_from, date_to
        )
        for slot in availability.slots:
            if not slot.date or slot.date < today_iso:
                continue
            capacity = slot.available_capacity or 0
            if slot.is_available and capacity >= _TEST_PARTY_SIZE:
                print(
                    f"  [find_slot] experience={exp.experience_id} "
                    f"slot={slot.slot_id} date={slot.date} capacity={capacity}"
                )
                return exp.experience_id, slot.slot_id, slot.date

    pytest.skip(
        "No se encontró ningún slot con disponibilidad. "
        "Verifica el ERP o amplía el rango de búsqueda."
    )


# ---------------------------------------------------------------------------
# get_cancellation_impact con un reservation_id válido
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_get_cancellation_impact.py::test_get_cancellation_impact
@pytest.mark.anyio
async def test_get_cancellation_impact(
    ctx: RunContext[AgentDeps],
    ctx_factory: Callable[..., RunContext[AgentDeps]],
) -> None:
    """Debe devolver un CancellationImpact válido para un ticket existente.

    Pasos:
    1. Descubre un slot disponible.
    2. Crea un ticket PENDING para obtener un reservation_id real.
    3. Llama a get_cancellation_impact y valida la respuesta.
    4. Cancela el ticket creado para limpiar el ERP.
    """
    # -- 1. Slot disponible --------------------------------------------------
    try:
        experience_id, slot_id, selected_date = await _find_available_slot(ctx)
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise
    print(f"\n  experience_id={experience_id}  slot_id={slot_id}  date={selected_date}")

    # -- 2. Crear reserva PENDING --------------------------------------------
    booking_ctx = ctx_factory(contact_id=_TEST_CONTACT_ID, user_name=_TEST_USER_NAME)

    ticket_id: str | None = None
    try:
        ticket_result = await create_pending_reservation(
            booking_ctx,
            experience_id=experience_id,
            slot_id=slot_id,
            party_size=_TEST_PARTY_SIZE,
            selected_date=selected_date,
        )
        assert isinstance(ticket_result, PendingTicket)
        ticket_id = ticket_result.ticket_id
        print(f"  ticket_id={ticket_id}  status={ticket_result.status}")
    except httpx.HTTPStatusError as exc:
        body = exc.response.json()
        error_msg = body.get("message", {}).get("error", {}).get("message", "")
        print(f"  [ERP error body create] {error_msg}")
        if "already exists" in error_msg:
            match = re.search(r"TKT-[\w-]+", error_msg)
            if match:
                ticket_id = match.group(0)
                print(f"  [create] reusing existing ticket_id={ticket_id}")
        if ticket_id is None:
            _skip_if_erp_unavailable(exc)
            raise
    except httpx.RequestError as exc:
        pytest.skip(f"No se pudo contactar al ERP: {exc}")

    assert ticket_id is not None

    # -- 3. Consultar impacto de cancelación ---------------------------------
    try:
        impact = await get_cancellation_impact(booking_ctx, reservation_id=ticket_id)
    except httpx.HTTPStatusError as exc:
        print(f"  [ERP error body impact] {exc.response.text}")
        _skip_if_erp_unavailable(exc)
        raise
    except httpx.RequestError as exc:
        pytest.skip(f"No se pudo contactar al ERP: {exc}")

    assert isinstance(impact, CancellationImpact), (
        "La respuesta debe ser un CancellationImpact, no un error string"
    )

    print(f"\n  can_cancel={impact.can_cancel}")
    print(f"  penalty={impact.penalty}  refund_amount={impact.refund_amount}")
    print(f"  consequences={impact.consequences}")

    assert impact.reservation_id == ticket_id
    assert isinstance(impact.can_cancel, bool)

    # -- 4. Limpiar: cancelar el ticket creado -------------------------------
    try:
        await cancel_reservation(booking_ctx, reservation_id=ticket_id, confirmed=True)
        print(f"  [cleanup] ticket {ticket_id} cancelado")
    except Exception as exc:
        print(f"  [cleanup] advertencia al cancelar: {exc}")

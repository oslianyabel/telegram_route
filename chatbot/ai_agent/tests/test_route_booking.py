# uv run pytest -s chatbot/ai_agent/tests/test_route_booking.py

"""Functional tests for route reservation tools against the real ERP API.

Controllers covered:
  - route_booking_controller (create_route_reservation, get_route_status)

Note:
  El ERP impone unicidad por (contact_id, experience, slot). Si el contacto de
  prueba ya tiene un ticket para el slot de la ruta, el test se omite con un
  mensaje explicativo. Para re-ejecutarlo, cancela o elimina el ticket existente
  en el ERP.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import PendingRouteBooking, RouteBookingStatus
from chatbot.ai_agent.tests.conftest import FakeWhatsAppClient, build_run_context
from chatbot.ai_agent.tools.booking import (
    create_route_reservation,
    get_route_booking_status,
)
from chatbot.ai_agent.tools.catalog import get_route_availability

# Contacto de prueba del ERP (mismo que usan los ejemplos oficiales de la API)
_TEST_CONTACT_ID = "123456789"
_TEST_ROUTE_ID = "ROUTE_01"
_TEST_PARTY_SIZE = 2


async def _find_available_route_date(
    ctx: RunContext[AgentDeps],
    route_id: str,
    party_size: int,
) -> str:
    """Busca la primera fecha con disponibilidad en las próximas 12 semanas."""
    start = date.today() + timedelta(days=7)
    for week in range(12):
        candidate = start + timedelta(weeks=week)
        result: dict[str, Any] = await get_route_availability(
            ctx, route_id, str(candidate), party_size
        )
        if result.get("available"):
            print(f"  [find_available_date] {candidate} disponible")
            return str(candidate)
        print(f"  [find_available_date] {candidate} sin disponibilidad")
    msg = f"No se encontró fecha disponible para {route_id} en las próximas 12 semanas"
    raise ValueError(msg)


# ---------------------------------------------------------------------------
# create_route_reservation → get_route_booking_status
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_route_booking.py::test_create_and_get_route_booking
@pytest.mark.anyio
async def test_create_and_get_route_booking(
    erp_client: Any,
) -> None:
    """Debe crear una reserva de ruta PENDING y luego consultar su estado.

    Verifica que no se lance ninguna excepción y muestra la respuesta del ERP.
    Los ticket_id de las experiencias deben estar disponibles en el status.
    """
    deps = AgentDeps(
        erp_client=erp_client,
        db_services=None,  # type: ignore[arg-type]
        whatsapp_client=FakeWhatsAppClient(),  # type: ignore[arg-type]
        user_phone="+598 99 000 000",
        user_name=None,
        contact_id=_TEST_CONTACT_ID,
        conversation_id=None,
    )
    ctx = build_run_context(deps)

    print(f"\n  contact_id={ctx.deps.contact_id}")

    # -- Buscar primera fecha con disponibilidad real --------------------------
    available_date = await _find_available_route_date(
        ctx, _TEST_ROUTE_ID, _TEST_PARTY_SIZE
    )
    print(f"  fecha disponible seleccionada={available_date}")

    # -- Crear reserva de ruta ------------------------------------------------
    try:
        result = await create_route_reservation(
            ctx,
            route_id=_TEST_ROUTE_ID,
            date_from=available_date,
            date_to=available_date,
            party_size=_TEST_PARTY_SIZE,
        )
    except ModelRetry as exc:
        if "already exists" in str(exc):
            pytest.skip(
                f"El contacto '{_TEST_CONTACT_ID}' ya tiene un ticket para el slot de "
                f"{_TEST_ROUTE_ID}. Cancela el ticket existente en el ERP para re-ejecutar. "
                f"Detalle: {exc}"
            )
        raise

    assert isinstance(result, PendingRouteBooking), (
        f"Expected PendingRouteBooking, got: {result}"
    )
    booking = result

    print(f"  route_booking_id={booking.route_booking_id}")
    print(f"  status={booking.status}")
    print(f"  total_price={booking.total_price}")
    print(f"  deposit_required={booking.deposit_required}")
    print(f"  deposit_amount={booking.deposit_amount}")
    print(f"  tickets_count={booking.tickets_count}")
    print(f"  tickets={booking.tickets}")

    assert isinstance(booking, PendingRouteBooking)
    assert booking.route_booking_id
    assert booking.status == "PENDING"

    # -- Consultar estado de la reserva de ruta --------------------------------
    route_status = await get_route_booking_status(ctx, booking.route_booking_id)

    print(
        f"\n  [get_route_booking_status] route_booking_id={route_status.route_booking_id}"
    )
    print(f"  status={route_status.status}")
    print(f"  tickets_count={route_status.tickets_count}")
    print(f"  pending_count={route_status.pending_count}")
    print(f"  confirmed_count={route_status.confirmed_count}")
    print(f"  total_price={route_status.total_price}")
    print(f"  deposit_required={route_status.deposit_required}")
    print(f"  deposit_amount={route_status.deposit_amount}")
    for ticket in route_status.tickets:
        print(
            f"    ticket_id={ticket.ticket_id} experience={ticket.experience} "
            f"slot={ticket.slot} slot_date={ticket.slot_date} status={ticket.status}"
        )

    assert isinstance(route_status, RouteBookingStatus)
    assert route_status.route_booking_id == booking.route_booking_id
    assert len(route_status.tickets) > 0
    # Todos los tickets deben tener un ticket_id válido
    for ticket in route_status.tickets:
        assert ticket.ticket_id


# ---------------------------------------------------------------------------
# get_route_booking_status con booking existente
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_route_booking.py::test_get_route_booking_status_existing
@pytest.mark.anyio
async def test_get_route_booking_status_existing(
    erp_client: Any,
) -> None:
    """Consulta el estado de una reserva de ruta ya existente en el ERP.

    Verifica que no se lance ninguna excepción y muestra la respuesta completa.
    Se usa el route_booking_id creado por test_create_and_get_route_booking en
    la primera ejecución del suite. Ajusta _EXISTING_BOOKING_ID si cambia.
    """
    _EXISTING_BOOKING_ID = "RB-2026-03-00015"

    deps = AgentDeps(
        erp_client=erp_client,
        db_services=None,  # type: ignore[arg-type]
        whatsapp_client=FakeWhatsAppClient(),  # type: ignore[arg-type]
        user_phone="+598 99 000 000",
        user_name=None,
        contact_id=_TEST_CONTACT_ID,
        conversation_id=None,
    )
    ctx = build_run_context(deps)

    route_status = await get_route_booking_status(ctx, _EXISTING_BOOKING_ID)

    print(f"\n  route_booking_id={route_status.route_booking_id}")
    print(f"  route_id={route_status.route_id}")
    print(f"  status={route_status.status}")
    print(f"  tickets_count={route_status.tickets_count}")
    print(f"  pending_count={route_status.pending_count}")
    print(f"  confirmed_count={route_status.confirmed_count}")
    print(f"  total_price={route_status.total_price}")
    print(f"  deposit_required={route_status.deposit_required}")
    print(f"  deposit_amount={route_status.deposit_amount}")
    for ticket in route_status.tickets:
        print(
            f"    ticket_id={ticket.ticket_id} experience={ticket.experience} "
            f"slot={ticket.slot} slot_date={ticket.slot_date} status={ticket.status}"
        )

    assert isinstance(route_status, RouteBookingStatus)
    assert route_status.route_booking_id
    assert len(route_status.tickets) > 0
    for ticket in route_status.tickets:
        assert ticket.ticket_id

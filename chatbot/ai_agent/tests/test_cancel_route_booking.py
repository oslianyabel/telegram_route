# uv run pytest -s chatbot/ai_agent/tests/test_cancel_route_booking.py

"""Functional test for cancel_route_booking against the real ERP API.

Flujo cubierto:
  1. Usa get_route_availability para encontrar una fecha disponible.
  2. Crea una reserva de ruta PENDING con create_route_reservation.
  3. Llama a cancel_route_booking → debe retornar un string de éxito.
     (El ERP siempre lanza un error en la primera llamada, pero la cancelación
     se aplica igualmente — ver api_issues.md sección cancel_route_booking.)
  4. Verifica que el booking quede en estado CANCELLED consultando get_route_booking_status.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import PendingRouteBooking, RouteBookingStatus
from chatbot.ai_agent.tests.conftest import FakeWhatsAppClient, build_run_context
from chatbot.ai_agent.tools.booking import (
    cancel_route_booking,
    create_route_reservation,
    get_route_booking_status,
)
from chatbot.ai_agent.tools.catalog import get_route_availability

_TEST_CONTACT_ID = "+5351054484"
_TEST_USER_NAME = "Test Customer"
_TEST_ROUTE_ID = "ROUTE_01"
_TEST_PARTY_SIZE = 2
_TEST_CANCELLATION_REASON = "Test cancellation — automated test"


def _skip_if_erp_unavailable(exc: httpx.HTTPError) -> None:
    """Skip the test when the real ERP is temporarily unavailable."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500:
        pytest.skip(
            f"ERP temporalmente no disponible ({exc.response.status_code}) "
            f"en {exc.request.url}"
        )
    if isinstance(exc, httpx.RequestError):
        pytest.skip(f"No se pudo contactar al ERP: {exc}")


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
    pytest.skip(
        f"No se encontró fecha disponible para {route_id} en las próximas 12 semanas. "
        "Verifica el ERP o amplía el rango de búsqueda."
    )


# ---------------------------------------------------------------------------
# create_route_reservation → cancel_route_booking → get_route_booking_status
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_cancel_route_booking.py::test_create_and_cancel_route_booking
@pytest.mark.anyio
async def test_create_and_cancel_route_booking(
    erp_client: Any,
) -> None:
    """Debe crear una reserva de ruta PENDING y luego cancelarla exitosamente.

    Pasos:
    1. Busca una fecha disponible para la ruta de prueba.
    2. Crea la reserva de ruta PENDING con create_route_reservation.
    3. Cancela la reserva con cancel_route_booking → debe retornar un string de éxito.
       (El ERP siempre devuelve error en la 1ra llamada pero en realidad cancela —
        el bot detecta ese bug y lo trata como éxito.)
    4. Consulta get_route_booking_status → el status debe ser CANCELLED.
    """
    deps = AgentDeps(
        erp_client=erp_client,
        db_services=None,  # type: ignore[arg-type]
        whatsapp_client=FakeWhatsAppClient(),  # type: ignore[arg-type]
        user_phone="+598 99 000 000",
        user_name=_TEST_USER_NAME,
        contact_id=_TEST_CONTACT_ID,
        conversation_id=None,
    )
    ctx = build_run_context(deps)

    print(f"\n  contact_id={_TEST_CONTACT_ID}  route_id={_TEST_ROUTE_ID}")

    # -- 1. Buscar fecha disponible ------------------------------------------
    try:
        available_date = await _find_available_route_date(
            ctx, _TEST_ROUTE_ID, _TEST_PARTY_SIZE
        )
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise
    print(f"  fecha disponible={available_date}")

    # -- 2. Crear reserva de ruta PENDING ------------------------------------
    try:
        booking_result = await create_route_reservation(
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
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise

    assert isinstance(booking_result, PendingRouteBooking), (
        f"Expected PendingRouteBooking, got: {booking_result!r}"
    )
    booking = booking_result
    print(f"  route_booking_id={booking.route_booking_id}  status={booking.status}")
    print(f"  total_price={booking.total_price}  tickets_count={booking.tickets_count}")

    assert booking.route_booking_id
    assert booking.status == "PENDING"

    # -- 3. Cancelar la reserva de ruta ------------------------------------
    try:
        cancel_response = await cancel_route_booking(
            ctx,
            route_booking_id=booking.route_booking_id,
            cancellation_reason=_TEST_CANCELLATION_REASON,
        )
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise

    print(f"\n  [cancel_route_booking] respuesta: {cancel_response!r}")

    assert isinstance(cancel_response, str), (
        "cancel_route_booking debe retornar un string de confirmación"
    )
    assert booking.route_booking_id in cancel_response

    # -- 4. Verificar que el status sea CANCELLED en el ERP -----------------
    try:
        route_status = await get_route_booking_status(ctx, booking.route_booking_id)
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise

    print(f"\n  [get_route_booking_status] status={route_status.status}")
    for ticket in route_status.tickets:
        print(
            f"    ticket_id={ticket.ticket_id}  status={ticket.status}  "
            f"experience={ticket.experience}"
        )

    assert isinstance(route_status, RouteBookingStatus)
    assert route_status.route_booking_id == booking.route_booking_id
    assert route_status.status == "CANCELLED", (
        f"Se esperaba status CANCELLED, se obtuvo: {route_status.status!r}"
    )

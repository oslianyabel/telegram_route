# uv run pytest -s chatbot/ai_agent/tests/test_modify_route_booking.py

"""Functional tests for route modification tools against the real ERP API.

Flujo cubierto:
  1. Crea una reserva de ruta PENDING con create_route_reservation.
  2. Obtiene los ticket_id reales con get_route_booking_status.
  3. Llama a modify_route_booking_preview con un cambio de party_size en el
     primer ticket → verifica el preview (RouteModificationPreview).
  4. Llama a confirm_route_modification con el mismo cambio → verifica que
     el ERP confirma los tickets modificados.
  5. Cancela la reserva de ruta al final para no dejar estado sucio en el ERP.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
import pytest
from pydantic_ai import RunContext
from pydantic_ai.exceptions import ModelRetry

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    PendingRouteBooking,
    RouteModificationPreview,
    RouteTicketChange,
)
from chatbot.ai_agent.tests.conftest import FakeWhatsAppClient, build_run_context
from chatbot.ai_agent.tools.booking import (
    cancel_route_booking,
    confirm_route_modification,
    create_route_reservation,
    get_route_booking_status,
    modify_route_booking_preview,
)
from chatbot.ai_agent.tools.catalog import get_route_availability

_TEST_CONTACT_ID = "+5351054484"
_TEST_USER_NAME = "Test Customer"
_TEST_ROUTE_ID = "ROUTE_01"
_TEST_PARTY_SIZE = 2
_NEW_PARTY_SIZE = 3


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
# create_route_reservation → modify_route_booking_preview → confirm_route_modification
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_modify_route_booking.py::test_preview_and_confirm_route_modification
@pytest.mark.anyio
async def test_preview_and_confirm_route_modification(
    erp_client: Any,
) -> None:
    """Debe crear una reserva PENDING, previsualizar una modificación de party_size
    en el primer ticket, confirmarla y luego cancelar la reserva.

    Pasos:
    1. Busca una fecha disponible para la ruta de prueba.
    2. Crea la reserva PENDING con party_size=2.
    3. Obtiene el ticket_id del primer ticket con get_route_booking_status.
    4. Llama a modify_route_booking_preview con party_size=3 → verifica RouteModificationPreview.
    5. Llama a confirm_route_modification con el mismo cambio → verifica respuesta de éxito.
    6. Cancela la reserva para mantener el ERP limpio.
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
            ctx,
            _TEST_ROUTE_ID,
            _NEW_PARTY_SIZE,  # usar el tamaño mayor para garantizar disponibilidad
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

    # -- 3. Obtener ticket_id del primer ticket -------------------------------
    try:
        route_status = await get_route_booking_status(ctx, booking.route_booking_id)
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise

    assert len(route_status.tickets) > 0, "La reserva no tiene tickets"
    first_ticket = route_status.tickets[0]
    print(
        f"  primer ticket: ticket_id={first_ticket.ticket_id}  "
        f"slot={first_ticket.slot}  party_size={first_ticket.party_size}"
    )

    changes = [
        RouteTicketChange(ticket_id=first_ticket.ticket_id, party_size=_NEW_PARTY_SIZE)
    ]

    # -- 4. Preview de la modificación ---------------------------------------
    try:
        preview_result = await modify_route_booking_preview(
            ctx,
            route_booking_id=booking.route_booking_id,
            changes=changes,
        )
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise

    print(f"\n  [modify_route_booking_preview] resultado: {preview_result!r}")

    assert isinstance(preview_result, RouteModificationPreview), (
        f"Expected RouteModificationPreview, got: {preview_result!r}"
    )
    preview = preview_result
    assert preview.route_booking_id == booking.route_booking_id
    assert len(preview.changes) > 0

    change_preview = preview.changes[0]
    print(f"  ticket_id={change_preview.ticket_id}")
    print(
        f"  current_party_size={change_preview.current_party_size}  new_party_size={change_preview.new_party_size}"
    )
    print(f"  party_size_changed={change_preview.party_size_changed}")

    assert change_preview.ticket_id == first_ticket.ticket_id
    assert change_preview.party_size_changed is True
    assert change_preview.new_party_size == _NEW_PARTY_SIZE

    # -- 5. Confirmar la modificación ----------------------------------------
    try:
        confirm_response = await confirm_route_modification(
            ctx,
            route_booking_id=booking.route_booking_id,
            changes=changes,
        )
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise

    print(f"\n  [confirm_route_modification] respuesta: {confirm_response!r}")

    assert isinstance(confirm_response, str), (
        "confirm_route_modification debe retornar un string de confirmación"
    )
    assert booking.route_booking_id in confirm_response
    assert first_ticket.ticket_id in confirm_response

    # -- 6. Cancelar la reserva para mantener el ERP limpio ------------------
    try:
        await cancel_route_booking(
            ctx,
            route_booking_id=booking.route_booking_id,
            cancellation_reason="Automated test cleanup",
        )
        print(f"\n  [teardown] reserva {booking.route_booking_id} cancelada")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  [teardown] advertencia: no se pudo cancelar la reserva: {exc}")

# uv run pytest -s chatbot/ai_agent/tests/test_add_activities_to_route.py

"""Functional tests for adding activities to a route booking against the real ERP API.

Flujo cubierto:
  1. Crea una reserva de ruta PENDING con create_route_reservation.
  2. Llama a add_activities_to_route_preview para verificar el costo adicional
     de una experiencia extra → verifica AddActivitiesToRoutePreview.
  3. Llama a confirm_add_activities_to_route para confirmar la adición
     → verifica AddActivitiesToRouteResult con los nuevos ticket IDs.
  4. Cancela la reserva de ruta al final para no dejar estado sucio en el ERP.
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
    AddActivitiesToRoutePreview,
    AddActivitiesToRouteResult,
    PendingRouteBooking,
    RouteActivityInput,
)
from chatbot.ai_agent.tests.conftest import FakeWhatsAppClient, build_run_context
from chatbot.ai_agent.tools.booking import (
    add_activities_to_route_preview,
    cancel_route_booking,
    confirm_add_activities_to_route,
    create_route_reservation,
)
from chatbot.ai_agent.tools.catalog import get_route_availability

_TEST_CONTACT_ID = "+5351054484"
_TEST_USER_NAME = "Test Customer"
_TEST_ROUTE_ID = "ROUTE_01"
_TEST_PARTY_SIZE = 2
# Experiencia extra a agregar (tomada del JSON de ejemplo de la API)
_EXTRA_EXPERIENCE_ID = "EXP_POSITIVA"
_EXTRA_SLOT_ID = "SLOT_POSITIVA_01"


def _skip_if_erp_unavailable(exc: httpx.HTTPError) -> None:
    """Omite el test cuando el ERP está temporalmente no disponible."""
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
# add_activities_to_route_preview
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_add_activities_to_route.py::test_add_activities_preview
@pytest.mark.anyio
async def test_add_activities_preview(
    erp_client: Any,
) -> None:
    """Debe crear una reserva PENDING y obtener el preview del costo adicional
    de agregar una nueva actividad.

    Pasos:
    1. Busca una fecha disponible para la ruta de prueba.
    2. Crea la reserva PENDING con party_size=2.
    3. Llama a add_activities_to_route_preview con una experiencia extra.
    4. Verifica que la respuesta sea AddActivitiesToRoutePreview con precio y depósito.
    5. Cancela la reserva para mantener el ERP limpio.
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

    # -- 3. Preview de la adición de actividad --------------------------------
    activities = [
        RouteActivityInput(experience_id=_EXTRA_EXPERIENCE_ID, slot_id=_EXTRA_SLOT_ID)
    ]
    try:
        preview_result = await add_activities_to_route_preview(
            ctx,
            route_booking_id=booking.route_booking_id,
            activities=activities,
        )
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise
    finally:
        # Cancelar siempre para no dejar estado sucio
        try:
            await cancel_route_booking(
                ctx,
                route_booking_id=booking.route_booking_id,
                cancellation_reason="Test automatizado — limpieza post-preview",
            )
            print(f"  reserva {booking.route_booking_id} cancelada (cleanup)")
        except Exception as cleanup_exc:
            print(f"  [cleanup] error al cancelar: {cleanup_exc}")

    print(f"\n  [add_activities_to_route_preview] resultado: {preview_result!r}")

    # -- 4. Verificar respuesta -----------------------------------------------
    assert isinstance(preview_result, AddActivitiesToRoutePreview), (
        f"Expected AddActivitiesToRoutePreview, got: {preview_result!r}"
    )
    preview = preview_result
    assert preview.route_booking_id == booking.route_booking_id
    assert len(preview.activities_to_add) > 0, "El preview no contiene actividades"
    assert preview.total_additional_price is not None, "total_additional_price es None"
    assert preview.total_additional_price >= 0

    activity = preview.activities_to_add[0]
    print(f"  experience_id={activity.experience_id}")
    print(f"  price={activity.price}  deposit={activity.deposit}")
    print(f"  total_additional_price={preview.total_additional_price}")
    print(f"  total_additional_deposit={preview.total_additional_deposit}")

    assert activity.experience_id == _EXTRA_EXPERIENCE_ID
    assert activity.price is not None and activity.price >= 0


# ---------------------------------------------------------------------------
# add_activities_to_route_preview → confirm_add_activities_to_route
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_add_activities_to_route.py::test_preview_and_confirm_add_activities
@pytest.mark.anyio
async def test_preview_and_confirm_add_activities(
    erp_client: Any,
) -> None:
    """Debe crear una reserva PENDING, previsualizar la adición de una actividad
    extra, confirmarla y luego cancelar la reserva.

    Pasos:
    1. Busca una fecha disponible para la ruta de prueba.
    2. Crea la reserva PENDING con party_size=2.
    3. Llama a add_activities_to_route_preview → verifica AddActivitiesToRoutePreview.
    4. Llama a confirm_add_activities_to_route → verifica que se generaron nuevos tickets.
    5. Cancela la reserva para mantener el ERP limpio.
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

    activities = [
        RouteActivityInput(experience_id=_EXTRA_EXPERIENCE_ID, slot_id=_EXTRA_SLOT_ID)
    ]

    # -- 3. Preview -----------------------------------------------------------
    try:
        preview_result = await add_activities_to_route_preview(
            ctx,
            route_booking_id=booking.route_booking_id,
            activities=activities,
        )
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        await cancel_route_booking(
            ctx,
            route_booking_id=booking.route_booking_id,
            cancellation_reason="Test automatizado — limpieza post-error preview",
        )
        raise

    assert isinstance(preview_result, AddActivitiesToRoutePreview), (
        f"Expected AddActivitiesToRoutePreview, got: {preview_result!r}"
    )
    print(f"  preview total_additional_price={preview_result.total_additional_price}")
    print(f"  preview note={preview_result.note}")

    # -- 4. Confirmar adición -------------------------------------------------
    try:
        confirm_result = await confirm_add_activities_to_route(
            ctx,
            route_booking_id=booking.route_booking_id,
            activities=activities,
        )
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise
    finally:
        # Cancelar siempre para no dejar estado sucio
        try:
            await cancel_route_booking(
                ctx,
                route_booking_id=booking.route_booking_id,
                cancellation_reason="Test automatizado — limpieza post-confirmación",
            )
            print(f"  reserva {booking.route_booking_id} cancelada (cleanup)")
        except Exception as cleanup_exc:
            print(f"  [cleanup] error al cancelar: {cleanup_exc}")

    print(f"\n  [confirm_add_activities_to_route] resultado: {confirm_result!r}")

    # -- 5. Verificar respuesta -----------------------------------------------
    assert isinstance(confirm_result, AddActivitiesToRouteResult), (
        f"Expected AddActivitiesToRouteResult, got: {confirm_result!r}"
    )
    assert confirm_result.route_booking_id == booking.route_booking_id
    assert len(confirm_result.new_tickets) > 0, "No se generaron nuevos tickets"
    assert confirm_result.status is not None

    print(f"  new_tickets={confirm_result.new_tickets}")
    print(f"  status={confirm_result.status}")
    print(f"  tickets_count={confirm_result.tickets_count}")

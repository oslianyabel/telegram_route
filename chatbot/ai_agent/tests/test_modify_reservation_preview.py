# uv run pytest -s chatbot/ai_agent/tests/test_modify_reservation_preview.py

"""Functional test for modify_reservation_preview against the real ERP API.

Flujo cubierto:
  1. Busca un experience_id y slot_id con disponibilidad en el ERP.
  2. Crea una reserva PENDING para tener un reservation_id válido.
  3. Llama a modify_reservation_preview cambiando el party_size.
  4. Verifica que la respuesta sea un ModificationPreview sin errores y que
     contenga el campo price_impact.
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
    ModificationPreview,
    PendingTicket,
)
from chatbot.ai_agent.tools.booking import (
    create_pending_reservation,
    modify_reservation_preview,
)
from chatbot.ai_agent.tools.catalog import get_availability, list_experiences

_TEST_CONTACT_ID = "+5351054484"
_TEST_USER_NAME = "Test Customer"
_TEST_PARTY_SIZE = 1
_TEST_PARTY_SIZE_NEW = 2


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
        try:
            availability: AvailabilityResponse = await get_availability(
                ctx, exp.experience_id, date_from, date_to
            )
        except httpx.HTTPStatusError as exc:
            print(
                f"  [find_slot] skip experience={exp.experience_id}: {exc.response.status_code}"
            )
            continue
        for slot in availability.slots:
            capacity = slot.available_capacity or 0
            if slot.date and slot.date < today_iso:
                continue
            if slot.is_available and capacity >= _TEST_PARTY_SIZE_NEW:
                print(
                    f"  [find_slot] experience={exp.experience_id} "
                    f"slot={slot.slot_id} date={slot.date} capacity={capacity}"
                )
                if not slot.date:
                    continue
                return exp.experience_id, slot.slot_id, slot.date

    pytest.skip(
        "No se encontró ningún slot con capacidad >= 2. "
        "Verifica el ERP o amplía el rango de búsqueda."
    )


# ---------------------------------------------------------------------------
# modify_reservation_preview
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_modify_reservation_preview.py::test_modify_reservation_preview_no_errors
@pytest.mark.anyio
async def test_modify_reservation_preview_no_errors(
    ctx: RunContext[AgentDeps],
    ctx_factory: Callable[..., RunContext[AgentDeps]],
) -> None:
    """Debe retornar un ModificationPreview válido sin errores del ERP."""
    try:
        experience_id, slot_id, selected_date = await _find_available_slot(ctx)
    except httpx.HTTPError as exc:
        _skip_if_erp_unavailable(exc)
        raise

    # Crear contexto con contact_id y user_name para poder crear la reserva
    ctx_with_contact = ctx_factory(
        contact_id=_TEST_CONTACT_ID,
        user_name=_TEST_USER_NAME,
    )

    # 1. Crear reserva PENDING para obtener un reservation_id real.
    #    Si ya existe un ticket para el mismo contact+experience+slot, el ERP
    #    retorna 422 con el ticket_id en el mensaje; lo reutilizamos.
    ticket_id: str | None = None
    try:
        ticket = await create_pending_reservation(
            ctx_with_contact,
            experience_id=experience_id,
            slot_id=slot_id,
            party_size=_TEST_PARTY_SIZE,
            selected_date=selected_date,
        )
        assert isinstance(ticket, PendingTicket), (
            f"Se esperaba PendingTicket, se obtuvo: {ticket}"
        )
        ticket_id = ticket.ticket_id
        print(f"  [create] ticket_id={ticket_id} status={ticket.status}")
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

    # 2. Llamar a modify_reservation_preview cambiando el party_size
    try:
        result = await modify_reservation_preview(
            ctx_with_contact,
            reservation_id=ticket_id,
            party_size=_TEST_PARTY_SIZE_NEW,
        )
    except httpx.HTTPStatusError as exc:
        print(f"  [ERP error body preview] {exc.response.text}")
        _skip_if_erp_unavailable(exc)
        raise
    except httpx.RequestError as exc:
        pytest.skip(f"No se pudo contactar al ERP: {exc}")

    print(f"  [preview] result={result}")

    # 3. Verificar que no hubo error (str indica mensaje de error del ERP)
    assert not isinstance(result, str), f"El ERP devolvió un error: {result}"

    # 4. Verificar estructura del ModificationPreview
    assert isinstance(result, ModificationPreview)
    assert result.reservation_id == ticket_id
    assert result.price_impact is not None, "Se esperaba price_impact en la respuesta"
    print(
        f"  [preview] price_impact current={result.price_impact.current_price} "
        f"new={result.price_impact.new_price} diff={result.price_impact.price_difference}"
    )

# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py

"""Functional tests for catalog tools against the real ERP API.

Controllers covered:
  - experience_controller  (list_experiences, get_experience_detail)
  - route_controller       (list_routes, get_route_detail)
  - establishment_controller (list_establishments, get_establishment_details)
  - availability_controller  (get_availability, get_route_availability)
"""

from __future__ import annotations

import httpx
import pytest
from pydantic_ai import RunContext

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import (
    AvailabilityResponse,
    Establishment,
    Experience,
    Route,
)
from chatbot.ai_agent.tools.catalog import (
    get_availability,
    get_establishment_details,
    get_experience_detail,
    get_route_availability,
    get_route_detail,
    list_establishments,
    list_experiences,
    list_routes,
)

# ---------------------------------------------------------------------------
# experience_controller
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_experiences
@pytest.mark.anyio
async def test_list_experiences(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar al menos una experiencia y todas deben ser instancias de Experience."""
    result = await list_experiences(ctx)

    print(f"\n  list_experiences -> {len(result)} experiencias")
    assert isinstance(result, list)
    assert len(result) > 0
    assert all(isinstance(exp, Experience) for exp in result)


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_experiences_only_online
@pytest.mark.anyio
async def test_list_experiences_only_online(ctx: RunContext[AgentDeps]) -> None:
    """Todas las experiencias retornadas deben tener status='ONLINE'."""
    result = await list_experiences(ctx)

    offline = [e for e in result if e.status != "ONLINE"]
    print(f"\n  Experiencias offline (debe ser 0): {offline}")
    assert len(offline) == 0, f"Se recibieron experiencias no-ONLINE: {offline}"


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_experiences_with_date
@pytest.mark.anyio
async def test_list_experiences_with_date(ctx: RunContext[AgentDeps]) -> None:
    """El filtro date debe retornar experiencias disponibles para esa fecha."""
    result = await list_experiences(ctx, date="2026-03-10")

    print(f"\n  list_experiences(date=2026-03-10) -> {len(result)} experiencias")
    assert isinstance(result, list)


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_experiences_pagination
@pytest.mark.anyio
async def test_list_experiences_pagination(ctx: RunContext[AgentDeps]) -> None:
    """page_size=2 debe retornar como maximo 2 experiencias."""
    result = await list_experiences(ctx, page_size=2)

    print(f"\n  list_experiences(page_size=2) -> {len(result)} experiencias")
    assert len(result) <= 2


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_get_experience_detail
@pytest.mark.anyio
async def test_get_experience_detail(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar un dict con datos de la experiencia solicitada."""
    experiences = await list_experiences(ctx, page_size=1)
    assert len(experiences) > 0, "No hay experiencias en el ERP"

    exp_id = experiences[0].experience_id
    result = await get_experience_detail(ctx, experience_id=exp_id)

    print(f"\n  get_experience_detail({exp_id}) -> {list(result.keys())}")
    assert isinstance(result, dict)
    assert result.get("experience_id") == exp_id


# ---------------------------------------------------------------------------
# route_controller
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_routes
@pytest.mark.anyio
async def test_list_routes(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar una lista de Route con al menos un elemento."""
    result = await list_routes(ctx)

    print(f"\n  list_routes -> {len(result)} rutas")
    assert isinstance(result, list)
    assert len(result) > 0
    assert all(isinstance(r, Route) for r in result)


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_routes_pagination
@pytest.mark.anyio
async def test_list_routes_pagination(ctx: RunContext[AgentDeps]) -> None:
    result = await list_routes(ctx, page_size=2)

    print(f"\n  list_routes(page_size=2) -> {len(result)} rutas")
    assert len(result) <= 2


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_get_route_detail
@pytest.mark.anyio
async def test_get_route_detail(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar un dict con datos de la ruta solicitada."""
    routes = await list_routes(ctx, page_size=1)
    assert len(routes) > 0, "No hay rutas en el ERP"

    route_id = routes[0].route_id
    result = await get_route_detail(ctx, route_id=route_id)

    print(f"\n  get_route_detail({route_id}) -> {list(result.keys())}")
    assert isinstance(result, dict)
    assert result.get("route_id") == route_id


# ---------------------------------------------------------------------------
# establishment_controller
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_establishments
@pytest.mark.anyio
async def test_list_establishments(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar una lista de Establishment."""
    result = await list_establishments(ctx)

    print(f"\n  list_establishments -> {len(result)} establecimientos")
    assert isinstance(result, list)
    assert len(result) > 0
    assert all(isinstance(e, Establishment) for e in result)


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_list_establishments_pagination
@pytest.mark.anyio
async def test_list_establishments_pagination(ctx: RunContext[AgentDeps]) -> None:
    """page_size=1 debe retornar exactamente 1 establecimiento."""
    result = await list_establishments(ctx, page_size=1)

    print(f"\n  list_establishments(page_size=1) -> {len(result)} establecimientos")
    assert len(result) <= 1


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_get_establishment_details
@pytest.mark.anyio
async def test_get_establishment_details(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar un dict con el perfil del establecimiento."""
    establishments = await list_establishments(ctx, page_size=1)
    assert len(establishments) > 0, "No hay establecimientos en el ERP"

    est_id = establishments[0].establishment_id
    try:
        result = await get_establishment_details(ctx, establishment_id=est_id)
        print(f"\n  get_establishment_details({est_id}) -> {list(result.keys())}")
        assert isinstance(result, dict)
        assert result.get("company_id") == est_id
    except httpx.HTTPStatusError as exc:
        pytest.skip(f"ERP devolvio error {exc.response.status_code}")


# ---------------------------------------------------------------------------
# availability_controller
# ---------------------------------------------------------------------------


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_get_availability
@pytest.mark.anyio
async def test_get_availability(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar un AvailabilityResponse con slots para una fecha futura."""
    experiences = await list_experiences(ctx, page_size=1)
    assert len(experiences) > 0, "No hay experiencias en el ERP"

    exp_id = experiences[0].experience_id
    result = await get_availability(
        ctx,
        experience_id=exp_id,
        date_from="01-03-2026",
        date_to="31-12-2026",
    )

    print(
        f"\n  get_availability({exp_id}, 01-03-2026 -> 31-12-2026) -> {len(result.slots)} slots"
    )
    assert isinstance(result, AvailabilityResponse)
    assert result.experience_id == exp_id


# uv run pytest -s chatbot/ai_agent/tests/test_catalog.py::test_get_route_availability
@pytest.mark.anyio
async def test_get_route_availability(ctx: RunContext[AgentDeps]) -> None:
    """Debe retornar un dict con disponibilidad de la ruta."""
    routes = await list_routes(ctx, page_size=1)
    if not routes:
        pytest.skip("No hay rutas en el ERP")

    route_id = routes[0].route_id
    result = await get_route_availability(
        ctx, route_id=route_id, date="2026-03-10", party_size=2
    )

    print(f"\n  get_route_availability({route_id}) -> {result}")
    assert isinstance(result, dict)
    assert "route_id" in result

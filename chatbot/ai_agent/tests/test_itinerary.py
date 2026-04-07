# uv run pytest -s chatbot/ai_agent/tests/test_itinerary.py
"""
Test para la herramienta de consulta de itinerario del usuario.
Verifica que la API no devuelva errores y muestra la salida por consola.
"""

import pytest

from chatbot.ai_agent.dependencies import AgentDeps
from chatbot.ai_agent.models import CustomerItinerary
from chatbot.ai_agent.tools.booking import get_customer_itinerary


@pytest.mark.asyncio
async def test_get_customer_itinerary(monkeypatch):
    # Simula un cliente ERP y dependencias mínimas
    class DummyResponse:
        def __init__(self, json_data):
            self._json = json_data
            self.status_code = 200
            self.is_error = False

        def json(self):
            return self._json

        def raise_for_status(self):
            pass

    class DummyERPClient:
        async def post(self, url, json, timeout):
            # Simula respuesta de ejemplo
            return DummyResponse(
                {
                    "message": {
                        "success": True,
                        "data": {
                            "contact_id": "+5351054482",
                            "itinerary": [
                                {
                                    "type": "route",
                                    "route_id": "ROUTE_01",
                                    "route_name": "ROUTE_01",
                                    "reservations": [
                                        {
                                            "reservation_id": "TKT-2026-03-00042",
                                            "type": "route",
                                            "experience_id": "EXP_CREMERIE",
                                            "experience_name": "EXP_CREMERIE",
                                            "date": "None",
                                            "time": "None",
                                            "status": "PENDING",
                                            "party_size": 2,
                                            "qr_status": None,
                                            "checked_in": False,
                                            "checked_in_at": None,
                                        }
                                    ],
                                    "reservations_count": 1,
                                }
                            ],
                            "total_reservations": 1,
                            "upcoming_count": 0,
                            "completed_count": 0,
                        },
                    }
                }
            )

    deps = AgentDeps(
        erp_client=DummyERPClient(),  # type: ignore
        db_services=None,  # type: ignore
        whatsapp_client=None,  # type: ignore
        contact_id="+5351054482",
    )
    ctx = type("Ctx", (), {"deps": deps})()
    result = await get_customer_itinerary(ctx)  # type: ignore
    assert isinstance(result, CustomerItinerary)
    print("Itinerario obtenido:", result.model_dump_json(indent=2))


def test_itinerary_individual_reservation_normalised():
    """Reserva individual viene plana desde el ERP (sin lista 'reservations').
    El model_validator debe normalizar el item para que reservations tenga 1 elemento."""
    # uv run pytest -s chatbot/ai_agent/tests/test_itinerary.py::test_itinerary_individual_reservation_normalised
    raw = {
        "contact_id": "+5351054481",
        "itinerary": [
            {
                "reservation_id": "TKT-2026-03-00053",
                "type": "individual",
                "experience_id": "EXP_CREMERIE",
                "experience_name": "EXP_CREMERIE",
                "date": "2026-05-10",
                "time": "10:00",
                "status": "CONFIRMED",
                "party_size": 2,
                "qr_status": None,
                "checked_in": False,
                "checked_in_at": None,
                "deposit_status": None,
                "deposit_paid": 0,
                "deposit_required": 150,
            }
        ],
        "total_reservations": 1,
        "upcoming_count": 1,
        "completed_count": 0,
    }
    itinerary = CustomerItinerary.model_validate(raw)

    assert len(itinerary.itinerary) == 1
    item = itinerary.itinerary[0]
    assert len(item.reservations) == 1, (
        "La reserva individual debe estar en item.reservations"
    )

    res = item.reservations[0]
    assert res.reservation_id == "TKT-2026-03-00053"
    assert res.status == "CONFIRMED"

    # Simula la lógica de user_has_pending_deposit
    confirmed_ids = [
        r.reservation_id
        for i in itinerary.itinerary
        for r in i.reservations
        if r.status.lower() == "confirmed"
    ]
    assert confirmed_ids == ["TKT-2026-03-00053"], "Debe detectar el ticket CONFIRMED"
    print("confirmed_ids:", confirmed_ids)

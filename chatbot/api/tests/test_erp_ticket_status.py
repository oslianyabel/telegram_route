# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py

from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException

from chatbot.ai_agent.models import (
    ContactInfo,
    ERPTicketStatusRequest,
    ReservationContactDetail,
    ReservationExperienceDetail,
    ReservationSlotDetail,
    ReservationStatusDetail,
    TicketDecision,
)
from chatbot.api import erp_router
from chatbot.api.erp_router import (
    _build_ticket_message,
    _validate_ticket_status_payload,
)


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_ticket_status_request_normalizes_hyphenated_statuses() -> None:
    checked_in_request = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Checked-In",
    )
    no_show_request = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="No-Show",
    )

    assert checked_in_request.new_status == TicketDecision.CHECKED_IN
    assert no_show_request.new_status == TicketDecision.NO_SHOW


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_ticket_status_request_normalizes_completed_statuses() -> None:
    completed_request = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Completed",
    )
    completed_es_request = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Completado",
    )

    assert completed_request.new_status == TicketDecision.COMPLETED
    assert completed_es_request.new_status == TicketDecision.COMPLETED


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_validate_ticket_status_payload_accepts_matching_contact_and_status() -> None:
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Cancelled",
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="CANCELLED",
        contact=ReservationContactDetail(contact_id="CONTACT-1"),
    )

    _validate_ticket_status_payload(body=body, contact=contact, ticket=ticket)


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_validate_ticket_status_payload_rejects_ticket_from_other_contact() -> None:
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Rejected",
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="REJECTED",
        contact=ReservationContactDetail(contact_id="CONTACT-2"),
    )

    try:
        _validate_ticket_status_payload(body=body, contact=contact, ticket=ticket)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail == "El ticket TICKET-1 no pertenece al contacto CONTACT-1"
    else:
        raise AssertionError(
            "Se esperaba HTTPException cuando el ticket no pertenece al contacto"
        )


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_validate_ticket_status_payload_rejects_status_mismatch() -> None:
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Expired",  # type: ignore
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="REJECTED",
        contact=ReservationContactDetail(contact_id="CONTACT-1"),
    )

    try:
        _validate_ticket_status_payload(body=body, contact=contact, ticket=ticket)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "no coincide con el nuevo estado recibido" in exc.detail
    else:
        raise AssertionError(
            "Se esperaba HTTPException cuando el estado del ERP no coincide"
        )


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_build_ticket_message_supports_requested_statuses() -> None:
    expected_snippets = {
        TicketDecision.CANCELLED: "has been *cancelled*",
        TicketDecision.NO_SHOW: "*no show*",
        TicketDecision.EXPIRED: "has *expired*",
        TicketDecision.REJECTED: "has been *rejected*",
        TicketDecision.CHECKED_IN: "*check-in*",
    }

    for decision, snippet in expected_snippets.items():
        message = _build_ticket_message(decision, "TICKET-1", "Observacion.")

        assert "TICKET-1" in message
        assert snippet in message
        assert "Observacion." in message


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
@pytest.mark.asyncio
async def test_notify_ticket_status_completed_triggers_survey(monkeypatch) -> None:
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Completed",
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="COMPLETED",
        contact=ReservationContactDetail(contact_id="CONTACT-1"),
        experience=ReservationExperienceDetail(experience_id="EXP-1"),
        slot=ReservationSlotDetail(slot_id="SLOT-1", date=date.today().isoformat()),
    )
    sent_messages: list[tuple[str, str]] = []
    saved_messages: list[tuple[str, str, list[object]]] = []
    pending_surveys: list[tuple[str, object]] = []

    class DummyMessage:
        created_at = datetime.now(UTC).replace(tzinfo=None)

    async def fake_get_contact_by_id(contact_id: str) -> ContactInfo:
        assert contact_id == body.contact_id
        return contact

    async def fake_get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
        assert ticket_id == body.ticket_id
        return ticket

    async def fake_get_last_user_message(phone: str) -> DummyMessage:
        assert phone == contact.phone
        return DummyMessage()

    async def fake_send_text(*, user_number: str, text: str) -> bool:
        sent_messages.append((user_number, text))
        return True

    async def fake_save_assistant_msg(
        phone: str, text: str, attachments: list[object]
    ) -> None:
        saved_messages.append((phone, text, attachments))

    def fake_set_pending_survey(key: str, survey: object) -> None:
        pending_surveys.append((key, survey))

    monkeypatch.setattr(erp_router, "_get_contact_by_id", fake_get_contact_by_id)
    monkeypatch.setattr(
        erp_router, "_get_reservation_status", fake_get_reservation_status
    )
    monkeypatch.setattr(
        erp_router.services,
        "get_last_user_message",
        fake_get_last_user_message,
    )
    monkeypatch.setattr(erp_router.whatsapp_manager, "send_text", fake_send_text)
    monkeypatch.setattr(
        erp_router.message_handler,
        "save_assistant_msg",
        fake_save_assistant_msg,
    )
    monkeypatch.setattr(erp_router, "set_pending_survey", fake_set_pending_survey)

    result = await erp_router.notify_ticket_status(body)

    assert result == {"status": "survey_sent", "phone": contact.phone}
    assert sent_messages == [(contact.phone, erp_router.SURVEY_MESSAGE)]
    assert saved_messages == [(contact.phone, erp_router.SURVEY_MESSAGE, [])]
    assert len(pending_surveys) == 1
    assert pending_surveys[0][0] == contact.phone

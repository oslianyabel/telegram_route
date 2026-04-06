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
        new_status="Checked-In",  # type: ignore[arg-type]
    )
    no_show_request = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="No-Show",  # type: ignore[arg-type]
    )

    assert checked_in_request.new_status == TicketDecision.CHECKED_IN
    assert no_show_request.new_status == TicketDecision.NO_SHOW


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_ticket_status_request_normalizes_completed_statuses() -> None:
    completed_request = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Completed",  # type: ignore[arg-type]
    )
    completed_es_request = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status="Completado",  # type: ignore[arg-type]
    )

    assert completed_request.new_status == TicketDecision.COMPLETED
    assert completed_es_request.new_status == TicketDecision.COMPLETED


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py
def test_validate_ticket_status_payload_accepts_matching_contact_and_status() -> None:
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status=TicketDecision.CANCELLED,
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
        new_status=TicketDecision.REJECTED,
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
        new_status=TicketDecision.COMPLETED,
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

    async def fake_get_messages(phone: str) -> list:
        return []

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
    monkeypatch.setattr(erp_router.services, "get_messages", fake_get_messages)
    monkeypatch.setattr(
        erp_router.services,
        "get_last_user_message",
        fake_get_last_user_message,
    )
    monkeypatch.setattr(erp_router.whatsapp_manager, "send_text", fake_send_text)
    monkeypatch.setattr(erp_router, "save_msg", fake_save_assistant_msg)
    monkeypatch.setattr(erp_router, "set_pending_survey", fake_set_pending_survey)

    result = await erp_router.notify_ticket_status(body)

    assert result == {"status": "survey_sent", "phone": contact.phone}
    assert sent_messages == [(contact.phone, erp_router.SURVEY_MESSAGE)]
    assert saved_messages == [(contact.phone, erp_router.SURVEY_MESSAGE, [])]
    assert len(pending_surveys) == 1
    assert pending_surveys[0][0] == contact.phone


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py::test_notify_ticket_status_whatsapp_sends_message
@pytest.mark.asyncio
async def test_notify_ticket_status_whatsapp_sends_message(monkeypatch) -> None:
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status=TicketDecision.CANCELLED,
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="CANCELLED",
        contact=ReservationContactDetail(contact_id="CONTACT-1"),
    )
    sent_messages: list[tuple[str, str]] = []

    class DummyLastMsg:
        created_at = datetime.now(UTC).replace(tzinfo=None)

    async def fake_get_contact_by_id(contact_id: str) -> ContactInfo:
        return contact

    async def fake_get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
        return ticket

    async def fake_get_messages(phone: str) -> list:
        return []

    async def fake_get_last_user_message(phone: str) -> DummyLastMsg:
        return DummyLastMsg()

    async def fake_send_text(*, user_number: str, text: str) -> bool:
        sent_messages.append((user_number, text))
        return True

    monkeypatch.setattr(erp_router, "_get_contact_by_id", fake_get_contact_by_id)
    monkeypatch.setattr(
        erp_router, "_get_reservation_status", fake_get_reservation_status
    )
    monkeypatch.setattr(erp_router.services, "get_messages", fake_get_messages)
    monkeypatch.setattr(
        erp_router.services, "get_last_user_message", fake_get_last_user_message
    )
    monkeypatch.setattr(erp_router.whatsapp_manager, "send_text", fake_send_text)

    result = await erp_router.notify_ticket_status(body)

    assert result == {"status": "ok", "phone": contact.phone}
    assert len(sent_messages) == 1
    assert sent_messages[0][0] == contact.phone
    assert "cancelled" in sent_messages[0][1].lower()


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py::test_notify_ticket_status_whatsapp_window_expired_raises
@pytest.mark.asyncio
async def test_notify_ticket_status_whatsapp_window_expired_raises(monkeypatch) -> None:
    from datetime import timedelta

    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status=TicketDecision.REJECTED,
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="REJECTED",
        contact=ReservationContactDetail(contact_id="CONTACT-1"),
    )

    class OldMsg:
        created_at = (datetime.now(UTC) - timedelta(hours=25)).replace(tzinfo=None)

    async def fake_get_contact_by_id(contact_id: str) -> ContactInfo:
        return contact

    async def fake_get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
        return ticket

    async def fake_get_messages(phone: str) -> list:
        return []

    async def fake_get_last_user_message(phone: str) -> OldMsg:
        return OldMsg()

    monkeypatch.setattr(erp_router, "_get_contact_by_id", fake_get_contact_by_id)
    monkeypatch.setattr(
        erp_router, "_get_reservation_status", fake_get_reservation_status
    )
    monkeypatch.setattr(erp_router.services, "get_messages", fake_get_messages)
    monkeypatch.setattr(
        erp_router.services, "get_last_user_message", fake_get_last_user_message
    )

    try:
        await erp_router.notify_ticket_status(body)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "24h" in exc.detail or "expirado" in exc.detail
    else:
        raise AssertionError(
            "Se esperaba HTTPException cuando la ventana de 24h expiró"
        )


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py::test_notify_ticket_status_whatsapp_no_messages_raises
@pytest.mark.asyncio
async def test_notify_ticket_status_whatsapp_no_messages_raises(monkeypatch) -> None:
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status=TicketDecision.EXPIRED,
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="EXPIRED",
        contact=ReservationContactDetail(contact_id="CONTACT-1"),
    )

    async def fake_get_contact_by_id(contact_id: str) -> ContactInfo:
        return contact

    async def fake_get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
        return ticket

    async def fake_get_messages(phone: str) -> list:
        return []

    async def fake_get_last_user_message(phone: str) -> None:
        return None

    monkeypatch.setattr(erp_router, "_get_contact_by_id", fake_get_contact_by_id)
    monkeypatch.setattr(
        erp_router, "_get_reservation_status", fake_get_reservation_status
    )
    monkeypatch.setattr(erp_router.services, "get_messages", fake_get_messages)
    monkeypatch.setattr(
        erp_router.services, "get_last_user_message", fake_get_last_user_message
    )

    try:
        await erp_router.notify_ticket_status(body)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "ventana" in exc.detail or "mensajes" in exc.detail
    else:
        raise AssertionError(
            "Se esperaba HTTPException cuando no hay mensajes del usuario"
        )


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py::test_notify_ticket_status_telegram_sends_message
@pytest.mark.asyncio
async def test_notify_ticket_status_telegram_sends_message(monkeypatch) -> None:
    telegram_chat_id = "-100123456789"
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-TG-1",
        ticket_id="TICKET-1",
        new_status=TicketDecision.REJECTED,
    )
    contact = ContactInfo(contact_id="CONTACT-TG-1", phone=telegram_chat_id)
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="REJECTED",
        contact=ReservationContactDetail(contact_id="CONTACT-TG-1"),
    )
    telegram_sent: list[tuple[str, str]] = []

    async def fake_get_contact_by_id(contact_id: str) -> ContactInfo:
        return contact

    async def fake_get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
        return ticket

    async def fake_get_messages(phone: str) -> list:
        return []

    async def fake_send_telegram(*, chat_id: str, text: str) -> bool:
        telegram_sent.append((chat_id, text))
        return True

    monkeypatch.setattr(erp_router, "_get_contact_by_id", fake_get_contact_by_id)
    monkeypatch.setattr(
        erp_router, "_get_reservation_status", fake_get_reservation_status
    )
    monkeypatch.setattr(erp_router.services, "get_messages", fake_get_messages)
    monkeypatch.setattr(erp_router, "send_telegram", fake_send_telegram)

    result = await erp_router.notify_ticket_status(body)

    assert result == {"status": "ok", "chat_id": telegram_chat_id}
    assert len(telegram_sent) == 1
    assert telegram_sent[0][0] == telegram_chat_id
    assert "rejected" in telegram_sent[0][1].lower()


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py::test_notify_ticket_status_completed_rejects_future_slot
@pytest.mark.asyncio
async def test_notify_ticket_status_completed_rejects_future_slot(monkeypatch) -> None:
    """La fecha del ticket debe estar en el pasado (o ser hoy). Un slot futuro debe rechazarse."""
    from datetime import timedelta

    future_date = (date.today() + timedelta(days=1)).isoformat()
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-1",
        ticket_id="TICKET-1",
        new_status=TicketDecision.COMPLETED,
    )
    contact = ContactInfo(contact_id="CONTACT-1", phone="+59899000000")
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-1",
        status="COMPLETED",
        contact=ReservationContactDetail(contact_id="CONTACT-1"),
        experience=ReservationExperienceDetail(experience_id="EXP-1"),
        slot=ReservationSlotDetail(slot_id="SLOT-1", date=future_date),
    )

    class DummyMessage:
        created_at = datetime.now(UTC).replace(tzinfo=None)

    async def fake_get_contact_by_id(contact_id: str) -> ContactInfo:
        return contact

    async def fake_get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
        return ticket

    async def fake_get_messages(phone: str) -> list:
        return []

    async def fake_get_last_user_message(phone: str) -> DummyMessage:
        return DummyMessage()

    monkeypatch.setattr(erp_router, "_get_contact_by_id", fake_get_contact_by_id)
    monkeypatch.setattr(
        erp_router, "_get_reservation_status", fake_get_reservation_status
    )
    monkeypatch.setattr(erp_router.services, "get_messages", fake_get_messages)
    monkeypatch.setattr(
        erp_router.services, "get_last_user_message", fake_get_last_user_message
    )

    try:
        await erp_router.notify_ticket_status(body)
    except HTTPException as exc:
        assert exc.status_code == 422
        assert "todavía no ocurrió" in exc.detail or future_date in exc.detail
    else:
        raise AssertionError(
            "Se esperaba HTTPException cuando el slot todavía no ha ocurrido"
        )


# uv run pytest -s chatbot/api/tests/test_erp_ticket_status.py::test_notify_ticket_status_completed_telegram_triggers_survey
@pytest.mark.asyncio
async def test_notify_ticket_status_completed_telegram_triggers_survey(
    monkeypatch,
) -> None:
    """COMPLETED por canal Telegram debe enviar la encuesta por Telegram y registrar pending survey."""
    from chatbot.reminders.lead_followup import CHANNEL_MARKERS, CHANNEL_TELEGRAM

    telegram_chat_id = "-100123456789"
    body = ERPTicketStatusRequest(
        contact_id="CONTACT-TG-1",
        ticket_id="TICKET-TG-1",
        new_status=TicketDecision.COMPLETED,
    )
    contact = ContactInfo(contact_id="CONTACT-TG-1", phone=telegram_chat_id)
    ticket = ReservationStatusDetail(
        ticket_id="TICKET-TG-1",
        status="COMPLETED",
        contact=ReservationContactDetail(contact_id="CONTACT-TG-1"),
        experience=ReservationExperienceDetail(experience_id="EXP-TG-1"),
        slot=ReservationSlotDetail(slot_id="SLOT-TG-1", date=date.today().isoformat()),
    )
    telegram_sent: list[tuple[str, str]] = []
    saved_messages: list[tuple[str, str, list[object]]] = []
    pending_surveys: list[tuple[str, object]] = []

    class FakeTelegramMsg:
        role: str = "system"
        message: str = CHANNEL_MARKERS[CHANNEL_TELEGRAM]

    async def fake_get_contact_by_id(contact_id: str) -> ContactInfo:
        return contact

    async def fake_get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
        return ticket

    async def fake_get_messages(phone: str) -> list:
        return [FakeTelegramMsg()]

    async def fake_send_telegram(*, chat_id: str, text: str) -> bool:
        telegram_sent.append((chat_id, text))
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
    monkeypatch.setattr(erp_router.services, "get_messages", fake_get_messages)
    monkeypatch.setattr(erp_router, "send_telegram", fake_send_telegram)
    monkeypatch.setattr(erp_router, "save_msg", fake_save_assistant_msg)
    monkeypatch.setattr(erp_router, "set_pending_survey", fake_set_pending_survey)

    result = await erp_router.notify_ticket_status(body)

    assert result == {"status": "survey_sent", "chat_id": telegram_chat_id}
    assert telegram_sent == [(telegram_chat_id, erp_router.SURVEY_MESSAGE)]
    assert saved_messages == [(telegram_chat_id, erp_router.SURVEY_MESSAGE, [])]
    assert len(pending_surveys) == 1
    assert pending_surveys[0][0] == telegram_chat_id

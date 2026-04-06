import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, status

from chatbot.ai_agent.agent import PROMPT_FILE, reset_cheese_agent
from chatbot.ai_agent.models import (
    ERP_BASE_PATH,
    ContactInfo,
    ERPSendMessageRequest,
    ERPSendTelegramRequest,
    ERPSurveyRequest,
    ERPTelegramControlRequest,
    ERPTicketStatusRequest,
    ERPWhatsAppControlRequest,
    PaymentInstructions,
    ReservationStatusDetail,
    TicketDecision,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data
from chatbot.api.utils.message_handler import save_assistant_msg as save_msg
from chatbot.api.utils.security import get_api_key
from chatbot.api.utils.survey_feedback import PendingSurvey, set_pending_survey
from chatbot.api.whatsapp_router import erp_client
from chatbot.core import human_control
from chatbot.db.services import services
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.telegram_notifier import send_message as send_telegram
from chatbot.messaging.whatsapp import whatsapp_manager
from chatbot.reminders.lead_followup import CHANNEL_TELEGRAM, infer_channel

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_api_key)])

ERP_TIMEOUT: float = 15.0
WHATSAPP_WINDOW_HOURS: int = 24
SURVEY_MESSAGE: str = (
    "We'd love to hear your feedback about the experience you just completed. "
    "Please reply with a rating from 1 to 5 and, if you want, a short comment.\n\n"
)

# ---------------------------------------------------------------------------
# Mensajes de notificación de estado de ticket
# ---------------------------------------------------------------------------

_TICKET_MESSAGES: dict[TicketDecision, str] = {
    TicketDecision.APPROVED: (
        "✅ Good news! Your reservation *{ticket_id}* has been *confirmed* by the establishment. "
        "{observations}"
        "To complete your reservation, please pay the deposit using the instructions below. We look forward to welcoming you! 🧀"
    ),
    TicketDecision.CANCELLED: (
        "Your reservation *{ticket_id}* has been *cancelled*. "
        "{observations}"
        "If you need to book again, send us a message and we'll gladly help you."
    ),
    TicketDecision.NO_SHOW: (
        "Your reservation *{ticket_id}* was marked as *no show* because no attendance was recorded. "
        "{observations}"
        "If you believe this is a mistake, contact us and we'll review it."
    ),
    TicketDecision.REJECTED: (
        "We're sorry, your reservation *{ticket_id}* has been *rejected*. "
        "{observations}"
        "If you have any questions, send us a message and we'll gladly help you."
    ),
    TicketDecision.EXPIRED: (
        "Your reservation *{ticket_id}* has *expired* because it was not confirmed in time. "
        "{observations}"
        "You can make a new reservation whenever you want. 😊"
    ),
    TicketDecision.CHECKED_IN: (
        "We have registered your *check-in* for reservation *{ticket_id}*. "
        "{observations}"
        "We hope you enjoy the experience."
    ),
}


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------


async def _get_contact_by_id(contact_id: str) -> ContactInfo:
    """Obtiene la información de un contacto desde el ERP usando su contact_id.

    Args:
        contact_id: Identificador del contacto en el ERP.

    Returns:
        ContactInfo con los datos del contacto, incluyendo el teléfono.

    Raises:
        HTTPException 502: Si el ERP no responde o devuelve un error.
        HTTPException 404: Si el contacto no existe en el ERP.
    """
    logger.debug("[_get_contact_by_id] contact_id=%s", contact_id)
    try:
        response = await erp_client.post(
            f"{ERP_BASE_PATH}.contact_controller.get_contact",
            json={"contact_id": contact_id},
            timeout=ERP_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[_get_contact_by_id] ERP HTTP error for contact_id=%s: %s",
            contact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ERP error al obtener contacto: {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        logger.error(
            "[_get_contact_by_id] ERP request error for contact_id=%s: %s",
            contact_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo conectar con el ERP",
        ) from exc

    data: Any = extract_erp_data(response.json())
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contacto {contact_id} no encontrado en el ERP",
        )

    contact = ContactInfo.model_validate(data)
    logger.debug(
        "[_get_contact_by_id] contact_id=%s phone=%s", contact_id, contact.phone
    )
    return contact


async def _get_experience_detail(experience_id: str) -> dict[str, Any]:
    """Obtiene el detalle de una experiencia desde el ERP para validar su existencia."""
    logger.debug("[_get_experience_detail] experience_id=%s", experience_id)
    try:
        response = await erp_client.post(
            f"{ERP_BASE_PATH}.experience_controller.get_experience_detail",
            json={"experience_id": experience_id},
            timeout=ERP_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[_get_experience_detail] ERP HTTP error for experience_id=%s: %s",
            experience_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ERP error al obtener experiencia: {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        logger.error(
            "[_get_experience_detail] ERP request error for experience_id=%s: %s",
            experience_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo conectar con el ERP",
        ) from exc

    data: Any = extract_erp_data(response.json())
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiencia {experience_id} no encontrada en el ERP",
        )

    if isinstance(data, dict):
        returned_experience_id = data.get("experience_id")
        if returned_experience_id and returned_experience_id != experience_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"La experiencia recibida ({experience_id}) no coincide con la devuelta por el ERP "
                    f"({returned_experience_id})"
                ),
            )

    return data


async def _get_reservation_status(ticket_id: str) -> ReservationStatusDetail:
    """Obtiene el estado detallado de una reserva para validar ticket, contacto, experiencia y slot."""
    logger.debug("[_get_reservation_status] ticket_id=%s", ticket_id)
    try:
        response = await erp_client.post(
            f"{ERP_BASE_PATH}.ticket_controller.get_reservation_status",
            json={"reservation_id": ticket_id},
            timeout=ERP_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[_get_reservation_status] ERP HTTP error for ticket_id=%s: %s",
            ticket_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ERP error al obtener ticket: {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        logger.error(
            "[_get_reservation_status] ERP request error for ticket_id=%s: %s",
            ticket_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo conectar con el ERP",
        ) from exc

    data: Any = extract_erp_data(response.json())
    if not data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {ticket_id} no encontrado en el ERP",
        )

    return ReservationStatusDetail.model_validate(data)


def _is_within_whatsapp_window(last_user_message_created_at: datetime) -> bool:
    """Verifica si el timestamp del último mensaje del usuario está dentro de la ventana de 24h de META.

    Args:
        last_user_message_created_at: Fecha/hora del último mensaje del usuario.

    Returns:
        True si el mensaje es más reciente que now - 24h.
    """
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        hours=WHATSAPP_WINDOW_HOURS
    )
    return last_user_message_created_at >= cutoff


def _build_ticket_message(
    decision: TicketDecision, ticket_id: str, observations: str | None
) -> str:
    """Construye el texto del mensaje de WhatsApp para una decisión de ticket.

    Args:
        decision: Estado de la decisión (approved/rejected/expired).
        ticket_id: ID del ticket afectado.
        observations: Observaciones opcionales del operador.

    Returns:
        Texto formateado listo para enviar por WhatsApp.
    """
    obs_text = f"{observations} " if observations else ""
    template = _TICKET_MESSAGES[decision]
    return template.format(ticket_id=ticket_id, observations=obs_text)


def _normalize_ticket_status(status_value: str | None) -> str | None:
    """Normaliza estados del ERP para compararlos sin depender del formato recibido."""
    if status_value is None:
        return None

    return status_value.strip().upper().replace("-", "_").replace(" ", "_")


def _validate_ticket_status_payload(
    body: ERPTicketStatusRequest,
    contact: ContactInfo,
    ticket: ReservationStatusDetail,
) -> None:
    """Valida que el ticket consultado pertenezca al contacto y tenga el estado informado."""
    if contact.contact_id != body.contact_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El contacto devuelto por el ERP ({contact.contact_id}) no coincide con "
                f"el contact_id recibido ({body.contact_id})"
            ),
        )

    if ticket.ticket_id != body.ticket_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El ticket devuelto por el ERP ({ticket.ticket_id}) no coincide con "
                f"el ticket_id recibido ({body.ticket_id})"
            ),
        )

    ticket_contact_id = ticket.contact.contact_id if ticket.contact else None
    if ticket_contact_id != body.contact_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"El ticket {body.ticket_id} no pertenece al contacto {body.contact_id}",
        )

    ticket_status = _normalize_ticket_status(ticket.status)
    expected_status = _normalize_ticket_status(body.new_status.value)
    if ticket_status != expected_status:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El ticket {body.ticket_id} tiene estado {ticket.status or 'desconocido'} en el ERP "
                f"y no coincide con el nuevo estado recibido ({body.new_status.value})"
            ),
        )


def _validate_activity_completed_payload(
    body: ERPSurveyRequest,
    contact: ContactInfo,
    ticket: ReservationStatusDetail,
) -> None:
    """Valida que los IDs del webhook ERP sean consistentes con la reserva completada."""
    if contact.contact_id != body.contact_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El contacto devuelto por el ERP ({contact.contact_id}) no coincide con "
                f"el contact_id recibido ({body.contact_id})"
            ),
        )

    ticket_contact_id = ticket.contact.contact_id if ticket.contact else None
    if ticket_contact_id != body.contact_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"El ticket {body.ticket_id} no pertenece al contacto {body.contact_id}",
        )

    ticket_experience_id = (
        ticket.experience.experience_id if ticket.experience else None
    )
    if ticket_experience_id != body.experience_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El ticket {body.ticket_id} no corresponde a la experiencia {body.experience_id}"
            ),
        )

    ticket_slot_id = ticket.slot.slot_id if ticket.slot else None
    if ticket_slot_id != body.slot_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"El ticket {body.ticket_id} no corresponde al slot {body.slot_id}",
        )

    slot_date_str = ticket.slot.date if ticket.slot else None
    if not slot_date_str:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"El slot {body.slot_id} no tiene una fecha válida en el ERP",
        )

    try:
        slot_date = date.fromisoformat(slot_date_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Fecha de slot inválida recibida del ERP: {slot_date_str}",
        ) from exc

    if slot_date > date.today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El slot {body.slot_id} todavía no ocurrió; no se puede solicitar encuesta "
                f"antes de la fecha {slot_date_str}"
            ),
        )


def _build_activity_completed_request(
    contact_id: str, ticket: ReservationStatusDetail
) -> ERPSurveyRequest:
    """Construye el payload de encuesta usando los datos ya resueltos del ticket."""
    experience_id = ticket.experience.experience_id if ticket.experience else None
    if not experience_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El ticket {ticket.ticket_id} no contiene una experiencia válida "
                "para disparar la encuesta"
            ),
        )

    slot_id = ticket.slot.slot_id if ticket.slot else None
    if not slot_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"El ticket {ticket.ticket_id} no contiene un slot válido "
                "para disparar la encuesta"
            ),
        )

    return ERPSurveyRequest(
        contact_id=contact_id,
        experience_id=experience_id,
        slot_id=slot_id,
        ticket_id=ticket.ticket_id,
    )


async def _dispatch_activity_completed_survey(
    body: ERPSurveyRequest,
    contact: ContactInfo,
    ticket: ReservationStatusDetail,
    *,
    channel: str,
    telegram_chat_id: str | None = None,
) -> dict[str, str]:
    """Envía la encuesta luego de validar la consistencia de la reserva completada."""
    _validate_activity_completed_payload(body=body, contact=contact, ticket=ticket)
    phone = contact.phone
    if not phone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El contacto {body.contact_id} no tiene teléfono registrado en el ERP",
        )

    if channel == "whatsapp":
        last_msg = await services.get_last_user_message(phone)
        if last_msg is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"No hay mensajes del usuario {phone} en la base de datos. "
                    "La ventana de 24h de META no está activa."
                ),
            )

        if not _is_within_whatsapp_window(last_msg.created_at):  # type: ignore[attr-defined]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"La ventana de mensajes gratuitos de 24h de META para {phone} ha expirado. "
                    "El último mensaje del usuario fue hace más de 24 horas."
                ),
            )

        ok = await whatsapp_manager.send_text(
            user_number=phone,
            text=SURVEY_MESSAGE,
        )
        if not ok:
            logger.error(
                "[_dispatch_activity_completed_survey] Error enviando encuesta de satisfacción por WhatsApp a %s",
                phone,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error al enviar la encuesta de satisfacción a {phone}",
            )

        set_pending_survey(
            phone,
            PendingSurvey(
                contact_id=body.contact_id,
                experience_id=body.experience_id,
                slot_id=body.slot_id,
                ticket_id=body.ticket_id,
            ),
        )
        await save_msg(phone, SURVEY_MESSAGE, [])
        return {"status": "survey_sent", "phone": phone}

    if channel == "telegram":
        if not telegram_chat_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="telegram_chat_id es obligatorio para enviar la encuesta por Telegram",
            )

        ok = await send_telegram(chat_id=telegram_chat_id, text=SURVEY_MESSAGE)
        if not ok:
            logger.error(
                "[_dispatch_activity_completed_survey] Error enviando encuesta de satisfacción por Telegram a chat_id=%s",
                telegram_chat_id,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error al enviar la encuesta de satisfacción al chat {telegram_chat_id}",
            )

        set_pending_survey(
            telegram_chat_id,
            PendingSurvey(
                contact_id=body.contact_id,
                experience_id=body.experience_id,
                slot_id=body.slot_id,
                ticket_id=body.ticket_id,
            ),
        )
        await save_msg(telegram_chat_id, SURVEY_MESSAGE, [])
        return {"status": "survey_sent", "chat_id": telegram_chat_id}

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"Canal de encuesta no soportado: {channel}",
    )


async def _send_payment_instructions(
    phone: str,
    ticket_id: str,
    *,
    channel: str = "whatsapp",
) -> None:
    """Obtiene las instrucciones de pago del depósito y las envía al cliente.

    Args:
        phone: Número de teléfono / chat ID del cliente.
        ticket_id: Identificador del ticket cuyo depósito se debe pagar.
        channel: Canal de envío ("whatsapp" o "telegram").
    """
    logger.info(
        "[_send_payment_instructions] phone=%s ticket_id=%s channel=%s",
        phone,
        ticket_id,
        channel,
    )
    try:
        pay_resp = await erp_client.post(
            f"{ERP_BASE_PATH}.deposit_controller.get_deposit_instructions",
            json={"ticket_id": ticket_id},
            timeout=ERP_TIMEOUT,
        )
        pay_resp.raise_for_status()
        pay_data = extract_erp_data(pay_resp.json())
        pay_info = PaymentInstructions.model_validate(pay_data)
    except Exception as exc:
        logger.error(
            "[_send_payment_instructions] Error al obtener instrucciones de pago ticket=%s: %s",
            ticket_id,
            exc,
        )
        await notify_error(
            exc,
            context=f"_send_payment_instructions | ticket={ticket_id} | phone={phone}",
        )
        return

    lines = [
        "💳 Deposit payment instructions",
        f"Ticket: {pay_info.ticket_id}",
        f"Required amount: {pay_info.amount_required} UYU",
        f"Amount paid: {pay_info.amount_paid or 0} UYU",
        f"Amount remaining: {pay_info.amount_remaining} UYU",
    ]
    if pay_info.instructions:
        lines.append(f"\n{pay_info.instructions}")
    lines.append(
        f"\n📎 Send the payment receipt with the number {ticket_id} "
        "as the image or document caption."
    )

    pay_msg = "\n".join(lines)

    if channel == CHANNEL_TELEGRAM:
        ok = await send_telegram(chat_id=phone, text=pay_msg)
    else:
        ok = await whatsapp_manager.send_text(user_number=phone, text=pay_msg)

    if not ok:
        logger.error(
            "[_send_payment_instructions] Error enviando instrucciones a %s", phone
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/send-whatsapp", summary="Enviar mensaje de WhatsApp a un contacto")
async def send_whatsapp_message(body: ERPSendMessageRequest) -> dict[str, str]:
    """Recibe un contact_id y un mensaje, y lo envía por WhatsApp al contacto.

    Verifica que el contacto tenga una ventana de conversación activa (24 h) en
    META antes de enviar. Si la ventana está cerrada retorna un error 422.

    Body:
        - contact_id: ID del contacto en el ERP.
        - message: Texto a enviar por WhatsApp.
    """
    logger.info("[send-whatsapp] contact_id=%s", body.contact_id)

    # 1. Obtener teléfono del contacto desde el ERP
    contact = await _get_contact_by_id(body.contact_id)
    if not contact.phone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"El contacto {body.contact_id} no tiene teléfono registrado en el ERP",
        )

    phone = contact.phone

    # 2. Verificar ventana de 24h de META
    last_msg = await services.get_last_user_message(phone)
    if last_msg is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No hay mensajes del usuario {phone} en la base de datos. "
                "La ventana de 24h de META no está activa."
            ),
        )

    if not _is_within_whatsapp_window(last_msg.created_at):  # type: ignore[attr-defined]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"La ventana de mensajes gratuitos de 24h de META para {phone} ha expirado. "
                "El último mensaje del usuario fue hace más de 24 horas."
            ),
        )

    # 3. Enviar mensaje
    ok = await whatsapp_manager.send_text(user_number=phone, text=body.message)
    if not ok:
        logger.error("[send-whatsapp] Error enviando WhatsApp a %s", phone)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al enviar el mensaje de WhatsApp a {phone}",
        )

    logger.info("[send-whatsapp] Mensaje enviado a %s", phone)
    return {"status": "ok", "phone": phone}


@router.post("/send-telegram", summary="Enviar mensaje de Telegram a un usuario")
async def send_telegram_message(body: ERPSendTelegramRequest) -> dict[str, str]:
    """Recibe un contact_id (Telegram chat ID) y un mensaje, y lo envía por Telegram.

    Body:
        - contact_id: Telegram chat ID del destinatario.
        - message: Texto a enviar por Telegram.
    """
    logger.info("[send-telegram] contact_id=%s", body.contact_id)

    ok = await send_telegram(chat_id=body.contact_id, text=body.message)
    if not ok:
        logger.error(
            "[send-telegram] Error enviando Telegram a contact_id=%s", body.contact_id
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al enviar el mensaje de Telegram al chat {body.contact_id}",
        )

    logger.info("[send-telegram] Mensaje enviado a contact_id=%s", body.contact_id)
    return {"status": "ok", "chat_id": body.contact_id}


@router.post("/ticket-status", summary="Notificar al cliente el estado de su reserva")
async def notify_ticket_status(body: ERPTicketStatusRequest) -> dict[str, str]:
    """Informa al cliente por WhatsApp cambios relevantes en el estado de su reserva.

    Body:
        - contact_id: ID del contacto en el ERP.
        - ticket_id: ID del ticket afectado.
        - new_status: Nuevo estado (confirmed | cancelled | no_show | rejected | expired | checked_in | completed).
        - observations: Texto adicional opcional del operador.
    """
    logger.info(
        "[ticket-status] contact_id=%s ticket_id=%s new_status=%s",
        body.contact_id,
        body.ticket_id,
        body.new_status,
    )

    # 1. Obtener teléfono del contacto
    contact = await _get_contact_by_id(body.contact_id)
    if not contact.phone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El contacto {body.contact_id} no tiene teléfono registrado en el ERP",
        )

    ticket = await _get_reservation_status(body.ticket_id)
    _validate_ticket_status_payload(body=body, contact=contact, ticket=ticket)

    # Validar que la fecha del ticket no esté en el pasado (solo para confirmaciones)
    slot_date: date | None = None
    if body.new_status == TicketDecision.APPROVED:
        slot_date_str = ticket.slot.date if ticket.slot else None
        if slot_date_str:
            try:
                slot_date = date.fromisoformat(slot_date_str)
                if slot_date < date.today():
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"El ticket {body.ticket_id} corresponde a la fecha {slot_date_str}, "
                            "que ya pasó. No se puede confirmar un ticket con fecha pasada."
                        ),
                    )
            except ValueError:
                logger.warning(
                    "[ticket-status] Fecha de slot inválida para ticket %s: %s",
                    body.ticket_id,
                    slot_date_str,
                )

    phone = contact.phone

    # 2. Inferir canal de comunicación del contacto
    messages = await services.get_messages(phone)
    if not messages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(f"No hay historial de mensajes del usuario {phone} con el bot"),
        )

    channel = infer_channel(conversation_id=phone, messages=messages)

    if body.new_status == TicketDecision.COMPLETED:
        survey_request = _build_activity_completed_request(body.contact_id, ticket)
        return await _dispatch_activity_completed_survey(
            survey_request,
            contact,
            ticket,
            channel=channel,
            telegram_chat_id=phone if channel == CHANNEL_TELEGRAM else None,
        )

    # 3. Construir el mensaje
    message = _build_ticket_message(body.new_status, body.ticket_id, body.observations)

    if channel == CHANNEL_TELEGRAM:
        ok = await send_telegram(chat_id=phone, text=message)
        if not ok:
            logger.error("[ticket-status] Error enviando Telegram a %s", phone)
            await notify_error(
                Exception(
                    f"Error al enviar notificación de ticket {body.ticket_id} a Telegram {phone}"
                ),
                context=f"notify_ticket_status | contact_id={body.contact_id}",
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Error al enviar el mensaje de Telegram al chat {phone}",
            )

        logger.info(
            "[ticket-status] Notificación enviada por Telegram a %s (ticket=%s status=%s)",
            phone,
            body.ticket_id,
            body.new_status,
        )

        if body.new_status == TicketDecision.APPROVED:
            await _send_payment_instructions(
                phone=phone, ticket_id=body.ticket_id, channel=CHANNEL_TELEGRAM
            )
            await services.register_confirmed_ticket(
                ticket_id=body.ticket_id, phone=phone, ticket_date=slot_date
            )

        return {"status": "ok", "chat_id": phone}

    # Canal WhatsApp: verificar ventana de 24h de META
    last_msg = await services.get_last_user_message(phone)

    if not _is_within_whatsapp_window(last_msg.created_at):  # type: ignore[attr-defined]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"La ventana de mensajes gratuitos de 24h de META para {phone} ha expirado. "
                "El último mensaje del usuario fue hace más de 24 horas."
            ),
        )

    ok = await whatsapp_manager.send_text(user_number=phone, text=message)
    if not ok:
        logger.error("[ticket-status] Error enviando WhatsApp a %s", phone)
        await notify_error(
            Exception(
                f"Error al enviar notificación de ticket {body.ticket_id} a {phone}"
            ),
            context=f"notify_ticket_status | contact_id={body.contact_id}",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al enviar el mensaje de WhatsApp a {phone}",
        )

    logger.info(
        "[ticket-status] Notificación enviada por WhatsApp a %s (ticket=%s status=%s)",
        phone,
        body.ticket_id,
        body.new_status,
    )

    # When the establishment approves the reservation, also send payment instructions
    # so the customer knows how much to pay and where.
    if body.new_status == TicketDecision.APPROVED:
        await _send_payment_instructions(phone=phone, ticket_id=body.ticket_id)
        await services.register_confirmed_ticket(
            ticket_id=body.ticket_id, phone=phone, ticket_date=slot_date
        )

    return {"status": "ok", "phone": phone}


# ---------------------------------------------------------------------------
# Endpoints de control humano de conversaciones
# ---------------------------------------------------------------------------


@router.post(
    "/take-control/whatsapp",
    summary="Tomar control de una conversación de WhatsApp",
)
async def take_whatsapp_control(body: ERPWhatsAppControlRequest) -> dict[str, str]:
    """Desactiva las respuestas automáticas del bot para el número de WhatsApp indicado.

    El operador podrá responder manualmente al cliente hasta que se llame
    a /release-control/whatsapp con el mismo número.

    Body:
        - phone: Número de WhatsApp del cliente (ej: +59899000000).
    """
    logger.info("[take-control/whatsapp] phone=%s", body.phone)
    human_control.take_whatsapp_control(body.phone)
    return {"status": "controlled", "phone": body.phone}


@router.post(
    "/release-control/whatsapp",
    summary="Ceder control de una conversación de WhatsApp al bot",
)
async def release_whatsapp_control(body: ERPWhatsAppControlRequest) -> dict[str, str]:
    """Reactiva las respuestas automáticas del bot para el número de WhatsApp indicado.

    Body:
        - phone: Número de WhatsApp del cliente (ej: +59899000000).
    """
    logger.info("[release-control/whatsapp] phone=%s", body.phone)
    human_control.release_whatsapp_control(body.phone)
    return {"status": "released", "phone": body.phone}


@router.post(
    "/take-control/telegram",
    summary="Tomar control de una conversación de Telegram",
)
async def take_telegram_control(body: ERPTelegramControlRequest) -> dict[str, str]:
    """Desactiva las respuestas automáticas del bot para el chat de Telegram indicado.

    El operador podrá responder manualmente al cliente hasta que se llame
    a /release-control/telegram con el mismo chat_id.

    Body:
        - chat_id: Telegram chat ID del cliente.
    """
    logger.info("[take-control/telegram] chat_id=%s", body.chat_id)
    human_control.take_telegram_control(body.chat_id)
    return {"status": "controlled", "chat_id": body.chat_id}


@router.post(
    "/release-control/telegram",
    summary="Ceder control de una conversación de Telegram al bot",
)
async def release_telegram_control(body: ERPTelegramControlRequest) -> dict[str, str]:
    """Reactiva las respuestas automáticas del bot para el chat de Telegram indicado.

    Body:
        - chat_id: Telegram chat ID del cliente.
    """
    logger.info("[release-control/telegram] chat_id=%s", body.chat_id)
    human_control.release_telegram_control(body.chat_id)
    return {"status": "released", "chat_id": body.chat_id}


# ---------------------------------------------------------------------------
# Endpoints de gestión del prompt del agente
# ---------------------------------------------------------------------------


@router.get("/prompt", summary="Obtener el prompt del agente")
async def get_agent_prompt() -> dict[str, str]:
    """Devuelve el contenido actual del prompt del agente principal desde static/prompt.txt."""
    logger.info("[get-prompt] Leyendo prompt desde %s", PROMPT_FILE)
    try:
        content = PROMPT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Archivo de prompt no encontrado",
        ) from exc
    return {"prompt": content}


@router.put("/prompt", summary="Actualizar el prompt del agente")
async def update_agent_prompt(
    prompt: str = Body(..., embed=True, description="Nuevo contenido del prompt"),
) -> dict[str, str]:
    """Reemplaza el contenido de static/prompt.txt y reinicia el singleton del agente.

    El próximo mensaje procesado por el agente usará el nuevo prompt.

    Body:
        - prompt: Texto completo del nuevo prompt.
    """
    logger.info("[update-prompt] Actualizando prompt (%d chars)", len(prompt))
    try:
        PROMPT_FILE.write_text(prompt, encoding="utf-8")
    except OSError as exc:
        logger.error("[update-prompt] Error escribiendo prompt: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al escribir el archivo de prompt: {exc}",
        ) from exc
    reset_cheese_agent()
    logger.info("[update-prompt] Prompt actualizado y agente reiniciado")
    return {"status": "ok", "chars": str(len(prompt))}

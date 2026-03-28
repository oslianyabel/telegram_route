import logging
from datetime import UTC, datetime, timedelta
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
    TicketDecision,
)
from chatbot.ai_agent.tools.erp_utils import extract_erp_data
from chatbot.api.utils.security import get_api_key
from chatbot.api.whatsapp_router import erp_client
from chatbot.core import human_control
from chatbot.db.services import services
from chatbot.messaging.telegram_notifier import notify_error
from chatbot.messaging.telegram_notifier import send_message as send_telegram
from chatbot.messaging.whatsapp import whatsapp_manager

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(get_api_key)])

ERP_TIMEOUT: float = 15.0
WHATSAPP_WINDOW_HOURS: int = 24

# ---------------------------------------------------------------------------
# Mensajes de notificación de estado de ticket
# ---------------------------------------------------------------------------

_TICKET_MESSAGES: dict[TicketDecision, str] = {
    TicketDecision.APPROVED: (
        "✅ ¡Buenas noticias! Tu reserva *{ticket_id}* ha sido *confirmada* por el establecimiento. "
        "{observations}"
        "Para completar la reserva, realizá el pago de la seña siguiendo las instrucciones que te enviamos a continuación. ¡Te esperamos! 🧀"
    ),
    TicketDecision.REJECTED: (
        "Lo sentimos, tu reserva *{ticket_id}* ha sido *rechazada*. "
        "{observations}"
        "Si tienes alguna pregunta, escríbenos y con gusto te ayudamos."
    ),
    TicketDecision.EXPIRED: (
        "Tu reserva *{ticket_id}* ha *expirado* por falta de confirmación. "
        "{observations}"
        "Puedes hacer una nueva reserva cuando lo desees. 😊"
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
    """Informa al cliente por WhatsApp la aprobación, rechazo o expiración de su reserva.

    Body:
        - contact_id: ID del contacto en el ERP.
        - ticket_id: ID del ticket afectado.
        - new_status: Nuevo estado (approved | rejected | expired).
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

    phone = contact.phone

    # 2. Construir y enviar el mensaje
    message = _build_ticket_message(body.new_status, body.ticket_id, body.observations)
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
        "[ticket-status] Notificación enviada a %s (ticket=%s status=%s)",
        phone,
        body.ticket_id,
        body.new_status,
    )

    # When the establishment approves the reservation, also send payment instructions
    # so the customer knows how much to pay and where.
    if body.new_status == TicketDecision.APPROVED:
        await _send_payment_instructions(phone=phone, ticket_id=body.ticket_id)
        await services.register_confirmed_ticket(
            ticket_id=body.ticket_id, phone=phone
        )

    return {"status": "ok", "phone": phone}


async def _send_payment_instructions(phone: str, ticket_id: str) -> None:
    """Obtiene las instrucciones de pago del depósito y las envía al cliente por WhatsApp.

    Args:
        phone: Número de WhatsApp del cliente.
        ticket_id: Identificador del ticket cuyo depósito se debe pagar.
    """
    logger.info(
        "[_send_payment_instructions] phone=%s ticket_id=%s", phone, ticket_id
    )
    try:
        pay_resp = await erp_client.post(
            f"{ERP_BASE_PATH}.deposit_controller.get_payment_link_or_instructions",
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
        "💳 Instrucciones de pago de la seña",
        f"Ticket: {pay_info.ticket_id}",
        f"Monto requerido: {pay_info.amount_required} UYU",
        f"Monto pagado: {pay_info.amount_paid or 0} UYU",
        f"Monto restante: {pay_info.amount_remaining} UYU",
    ]
    if pay_info.instructions:
        lines.append(f"\n{pay_info.instructions}")
    lines.append(
        f"\n📎 Enviá el comprobante de pago con el número {ticket_id} "
        "como descripción de la imagen o del documento."
    )

    pay_msg = "\n".join(lines)
    ok = await whatsapp_manager.send_text(user_number=phone, text=pay_msg)
    if not ok:
        logger.error(
            "[_send_payment_instructions] Error enviando instrucciones a %s", phone
        )


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


@router.post(
    "/activity-completed", summary="Enviar encuesta de satisfacción tras actividad"
)
async def activity_completed(body: ERPSurveyRequest) -> dict[str, str]:
    """Notifica que se completó una actividad y envía una encuesta de satisfacción.

    Body:
        - contact_id: ID del contacto en el ERP.
        - experience_id: ID de la experiencia completada.
        - slot_id: ID del slot en que se realizó la actividad.
        - ticket_id: ID del ticket asociado.

    TODO: Implementar lógica de encuesta de satisfacción.
    """
    logger.info(
        "[activity-completed] contact_id=%s experience_id=%s slot_id=%s ticket_id=%s",
        body.contact_id,
        body.experience_id,
        body.slot_id,
        body.ticket_id,
    )
    # Lógica pendiente de implementación
    return {"status": "pending_implementation"}


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

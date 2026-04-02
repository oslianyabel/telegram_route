import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from chatbot.ai_agent.models import PaymentReceipt
from chatbot.ai_agent.tools.ocr import (
    extract_payment_receipt,
    extract_payment_receipt_from_pdf,
)
from chatbot.audio.audio_converter import convert_ogg_to_mp3
from chatbot.audio.stt import AVAILABLE_AUDIO_FORMATS, transcribe_audio
from chatbot.core.config import config
from chatbot.messaging.whatsapp import API_BASE

logger = logging.getLogger(__name__)
META_REQUEST_TIMEOUT_SECONDS = 10.0
MEDIA_REQUEST_TIMEOUT_SECONDS = 20.0

# Regex to match ERP ticket IDs like TKT-2026-03-00018
_TICKET_ID_RE = re.compile(r"TKT-\d{4}-\d{2}-\d+", re.IGNORECASE)


def _get_meta_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {config.WHATSAPP_ACCESS_TOKEN}"}


async def _resolve_media_url(client: httpx.AsyncClient, media_id: str) -> str | None:
    """Resuelve la URL de descarga de un media de WhatsApp a partir de su ID.

    Args:
        client: Cliente HTTP async reutilizado.
        media_id: ID del media devuelto por el webhook de Meta.

    Returns:
        URL firmada para descargar el archivo, o None si no está disponible.
    """
    meta_resp = await client.get(
        f"{API_BASE}/{media_id}",
        headers=_get_meta_headers(),
        timeout=META_REQUEST_TIMEOUT_SECONDS,
    )
    meta_resp.raise_for_status()
    url: str | None = meta_resp.json().get("url")
    if not url:
        logger.error("No media url for media id %s", media_id)
    return url


@dataclass
class ParsedMessage:
    """Result of parsing an incoming webhook message.

    For text/audio messages, ``text`` is set and ``receipt`` is None.
    For image/PDF messages with ticket in caption, ``receipt``, ``ticket_id`` and
    ``media_file_path`` are set.
    For image/PDF messages WITHOUT ticket in caption, ``media_file_path`` and ``is_pdf``
    are set so the router can store the path and defer OCR until the ticket arrives.
    """

    user_number: str
    message_id: str
    text: str | None = None
    receipt: PaymentReceipt | None = None
    ticket_id: str | None = None
    media_file_path: str | None = None
    is_pdf: bool = False


async def extract_message_content(webhook_data: dict) -> ParsedMessage | None:
    try:
        entry = webhook_data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})

        metadata = value.get("metadata", {})
        phone_number_id = metadata.get("phone_number_id", "")

        if phone_number_id != config.WHATSAPP_PHONE_NUMBER_ID:
            logger.warning(
                f"Mensaje enviado hacia el numero con id: {phone_number_id}, "
                f"el id del bot es: {config.WHATSAPP_PHONE_NUMBER_ID}"
            )

        messages = value.get("messages")
        if not messages:
            logger.debug("No messages in webhook data")
            return None

        message = messages[0]
        message_id = message.get("id", "")
        user_number = message.get("from", "")

        message_type = message.get("type", "")
        media_types = {
            "video",
            "sticker",
            "location",
            "contacts",
        }
        if message_type in media_types:
            logger.warning(
                f"Skipping text extraction for media message type: {message_type}"
            )
            return None

        if message_type == "image":
            media_obj = message.get("image", {})
            media_id = media_obj.get("id")
            caption: str | None = media_obj.get("caption")
            if not media_id:
                logger.error("Image message without media id")
                return None

            headers = _get_meta_headers()
            timeout = httpx.Timeout(MEDIA_REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(timeout=timeout) as client:
                media_url = await _resolve_media_url(client, media_id)
                if not media_url:
                    return None

                try:
                    receipt, ticket_id, file_path = await _extract_image_from_message(
                        user_number=user_number,
                        media_url=media_url,
                        headers=headers,
                        client=client,
                        caption=caption,
                    )
                except Exception as exc:
                    logger.exception(f"Failed to download/process image: {exc}")
                    return None

            return ParsedMessage(
                user_number=user_number,
                message_id=message_id,
                receipt=receipt,
                ticket_id=ticket_id,
                media_file_path=file_path,
                is_pdf=False,
            )

        elif message_type == "document":
            doc_obj = message.get("document", {})
            mime_type: str = doc_obj.get("mime_type", "")
            if mime_type != "application/pdf":
                logger.warning(
                    f"Skipping document with unsupported mime_type: {mime_type}"
                )
                return None

            media_id = doc_obj.get("id")
            caption = doc_obj.get("caption")
            if not media_id:
                logger.error("Document message without media id")
                return None

            headers = _get_meta_headers()
            timeout = httpx.Timeout(MEDIA_REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(timeout=timeout) as client:
                media_url = await _resolve_media_url(client, media_id)
                if not media_url:
                    return None

                try:
                    receipt, ticket_id, file_path = await _extract_pdf_from_message(
                        user_number=user_number,
                        media_url=media_url,
                        headers=headers,
                        client=client,
                        caption=caption,
                    )
                except Exception as exc:
                    logger.exception(f"Failed to download/process PDF document: {exc}")
                    return None

            return ParsedMessage(
                user_number=user_number,
                message_id=message_id,
                receipt=receipt,
                ticket_id=ticket_id,
                media_file_path=file_path,
                is_pdf=True,
            )

        elif message_type == "audio":
            media_obj = message.get("audio", {})
            media_id = media_obj.get("id")
            if not media_id:
                logger.error("Audio message without media id")
                return None

            headers = _get_meta_headers()
            timeout = httpx.Timeout(MEDIA_REQUEST_TIMEOUT_SECONDS)
            async with httpx.AsyncClient(timeout=timeout) as client:
                media_url = await _resolve_media_url(client, media_id)
                if not media_url:
                    return None

                try:
                    incoming_msg = await _extract_voice_from_message(
                        message_id=message_id,
                        media_url=media_url,
                        headers=headers,
                        client=client,
                    )
                except Exception as exc:
                    logger.exception(f"Failed to download audio media: {exc}")
                    return None

        else:
            incoming_msg = _extract_text_from_message(message, user_number)

        if not incoming_msg:
            logger.warning("No text message founded")
            return None

        return ParsedMessage(
            user_number=user_number,
            message_id=message_id,
            text=incoming_msg,
        )

    except (IndexError, KeyError) as e:
        logger.error(f"Error extracting message data: {e}")
        return None


async def _extract_voice_from_message(
    message_id: str,
    media_url: str,
    headers: dict[str, str],
    client: httpx.AsyncClient,
) -> str:
    async with client.stream(
        "GET",
        media_url,
        headers=headers,
        timeout=MEDIA_REQUEST_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0]
        ext_map = {
            "audio/ogg": ".ogg",
            "audio/opus": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/amr": ".amr",
            "audio/wav": ".wav",
        }
        ext = ext_map.get(content_type, ".bin")

        voice_dir = create_or_retrieve_voice_dir()
        filename = f"{message_id}{ext}"
        file_path = voice_dir / filename
        with open(file_path, "wb") as fh:
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if chunk:
                    fh.write(chunk)

    logger.info(f"Saved voice note to {file_path}")

    if not config.USE_FFMPEG or ext in AVAILABLE_AUDIO_FORMATS:
        return transcribe_audio(str(file_path))

    mp3_path = file_path.with_suffix(".mp3")
    ok = await convert_ogg_to_mp3(input_path=file_path, output_path=mp3_path)
    if ok:
        logger.info(f"Converted {ext} to MP3 with ffmpeg")
        return transcribe_audio(str(mp3_path))

    logger.warning("FFmpeg conversion failed, using original file for transcription")
    return transcribe_audio(str(file_path))


def _extract_text_from_message(message: dict, user_number: str) -> str:
    message_type = message.get("type")

    if message_type == "text":
        return message.get("text", {}).get("body", "").strip()
    else:
        logger.warning(f"Unsupported message type: {message_type}")

    return ""


def create_or_retrieve_voice_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    voice_dir = repo_root / "static" / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    return voice_dir


def create_or_retrieve_images_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    images_dir = repo_root / "static" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    return images_dir


def create_or_retrieve_documents_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    documents_dir = repo_root / "static" / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    return documents_dir


async def _extract_image_from_message(
    user_number: str,
    media_url: str,
    headers: dict[str, str],
    client: httpx.AsyncClient,
    caption: str | None = None,
) -> tuple[PaymentReceipt | None, str | None, str]:
    """Descarga una imagen de WhatsApp y la guarda en static/images.

    Si el caption contiene un ticket ID ejecuta el OCR de inmediato y devuelve
    el PaymentReceipt junto al ticket.  Si no hay ticket en el caption, omite el
    OCR y devuelve (None, None, file_path) para que el router espere el ticket
    en el próximo mensaje antes de procesar el comprobante.

    Args:
        user_number: Número de teléfono del remitente (usado como nombre de archivo).
        media_url: URL firmada del archivo en la API de Meta.
        headers: Headers de autorización.
        client: Cliente HTTP async reutilizado.
        caption: Caption opcional del mensaje de imagen (puede contener ticket_id).

    Returns:
        Tuple (receipt | None, ticket_id | None, file_path).
    """
    ext_map: dict[str, str] = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    async with client.stream(
        "GET",
        media_url,
        headers=headers,
        timeout=MEDIA_REQUEST_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].strip()
        ext = ext_map.get(content_type, ".jpg")

        images_dir = create_or_retrieve_images_dir()
        file_path = images_dir / f"{user_number}{ext}"
        with open(file_path, "wb") as fh:
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if chunk:
                    fh.write(chunk)

    logger.info("[image] Saved image to %s", file_path)

    # Extract ticket_id from caption
    ticket_id: str | None = None
    if caption:
        match = _TICKET_ID_RE.search(caption)
        if match:
            ticket_id = match.group().upper()
            logger.info("[image] Extracted ticket_id=%s from caption", ticket_id)
        else:
            logger.debug("[image] Caption present but no ticket_id found: %r", caption)

    # Only run OCR if we already have the ticket — otherwise defer to next message
    if not ticket_id:
        logger.info(
            "[image] No ticket_id in caption for user=%s — deferring OCR", user_number
        )
        return None, None, str(file_path)

    receipt = await extract_payment_receipt(str(file_path))
    logger.info(
        "[ocr] Extracted receipt data — "
        "amount=%s | date=%s | reference=%s | account=%s | "
        "recipient_name=%s | payment_method=%s | branch=%s | concept=%s",
        receipt.amount,
        receipt.date,
        receipt.reference,
        receipt.account,
        receipt.recipient_name,
        receipt.payment_method,
        receipt.branch,
        receipt.concept,
    )
    return receipt, ticket_id, str(file_path)


async def _extract_pdf_from_message(
    user_number: str,
    media_url: str,
    headers: dict[str, str],
    client: httpx.AsyncClient,
    caption: str | None = None,
) -> tuple[PaymentReceipt | None, str | None, str]:
    """Descarga un PDF de WhatsApp y lo guarda en static/documents.

    Si el caption contiene un ticket ID ejecuta el OCR de inmediato y devuelve
    el PaymentReceipt junto al ticket.  Si no hay ticket en el caption, omite el
    OCR y devuelve (None, None, file_path) para que el router espere el ticket.

    Args:
        user_number: Número de teléfono del remitente (usado como nombre de archivo).
        media_url: URL firmada del archivo en la API de Meta.
        headers: Headers de autorización.
        client: Cliente HTTP async reutilizado.
        caption: Caption opcional del mensaje (puede contener ticket_id).

    Returns:
        Tuple (receipt | None, ticket_id | None, file_path).
    """
    async with client.stream(
        "GET",
        media_url,
        headers=headers,
        timeout=MEDIA_REQUEST_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()

        documents_dir = create_or_retrieve_documents_dir()
        file_path = documents_dir / f"{user_number}.pdf"
        with open(file_path, "wb") as fh:
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if chunk:
                    fh.write(chunk)

    logger.info("[pdf] Saved PDF to %s", file_path)

    # Extract ticket_id from caption
    ticket_id: str | None = None
    if caption:
        match = _TICKET_ID_RE.search(caption)
        if match:
            ticket_id = match.group().upper()
            logger.info("[pdf] Extracted ticket_id=%s from caption", ticket_id)
        else:
            logger.debug("[pdf] Caption present but no ticket_id found: %r", caption)

    # Only run OCR if we already have the ticket — otherwise defer to next message
    if not ticket_id:
        logger.info(
            "[pdf] No ticket_id in caption for user=%s — deferring OCR", user_number
        )
        return None, None, str(file_path)

    receipt = await extract_payment_receipt_from_pdf(str(file_path))
    logger.info(
        "[ocr] Extracted receipt data — "
        "amount=%s | date=%s | reference=%s | account=%s | "
        "recipient_name=%s | payment_method=%s | branch=%s | concept=%s",
        receipt.amount,
        receipt.date,
        receipt.reference,
        receipt.account,
        receipt.recipient_name,
        receipt.payment_method,
        receipt.branch,
        receipt.concept,
    )
    return receipt, ticket_id, str(file_path)


def _format_receipt_as_text(receipt: PaymentReceipt) -> str:
    """Convierte un PaymentReceipt en texto plano legible."""
    field_labels: list[tuple[str, str]] = [
        ("amount", "Monto"),
        ("date", "Fecha/Hora"),
        ("reference", "Referencia"),
        ("account", "Cuenta destino"),
        ("recipient_name", "Beneficiario"),
        ("payment_method", "Metodo de pago"),
        ("branch", "Sucursal"),
        ("concept", "Concepto"),
    ]
    lines: list[str] = []
    for field, label in field_labels:
        value: str | None = getattr(receipt, field, None)
        if value:
            lines.append(f"{label}: {value}")

    if not lines:
        return "No se pudo extraer informacion del comprobante."

    return "\n".join(lines)

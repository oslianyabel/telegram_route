# uv run pytest -s chatbot/api/tests/test_qr_utils.py

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from chatbot.ai_agent.models import ERP_BASE_PATH, ReservationQrData
from chatbot.api.utils.qr import (
    build_qr_caption,
    build_qr_image_url,
    fetch_reservation_qr,
)
from chatbot.api.utils.webhook_parser import create_or_retrieve_images_dir
from chatbot.erp.client import build_erp_client
from chatbot.messaging.whatsapp import WhatsAppManager


def test_build_qr_image_url_joins_host_and_relative_path() -> None:
    result = build_qr_image_url(
        "/files/qr-TKT-2026-03-00067aec8dc.png",
        erp_host="https://erp-cheese.example.com/",
    )

    assert (
        result == "https://erp-cheese.example.com/files/qr-TKT-2026-03-00067aec8dc.png"
    )


def test_build_qr_caption_includes_ticket_and_token() -> None:
    result = build_qr_caption(
        ticket_id="TKT-2026-03-00067",
        token="a6fC8IHgNohlJnuaCDr8iTuyFRmfqZeE",
    )

    assert "TKT-2026-03-00067" in result
    assert "a6fC8IHgNohlJnuaCDr8iTuyFRmfqZeE" in result


# uv run pytest -s chatbot/api/tests/test_qr_utils.py -k fetch_reservation_qr
@pytest.mark.asyncio
async def test_fetch_reservation_qr_returns_qr_data_for_ticket() -> None:
    erp_response = {
        "message": {
            "success": True,
            "message": "QR token retrieved successfully",
            "data": {
                "qr_token_id": "a6fC8IHgNohlJnuaCDr8iTuyFRmfqZeE",
                "token": "a6fC8IHgNohlJnuaCDr8iTuyFRmfqZeE",
                "ticket_id": "TKT-2026-03-00067",
                "status": "ACTIVE",
                "expires_at": None,
                "qr_image_url": "/files/qr-TKT-2026-03-00067aec8dc.png",
                "is_new": False,
            },
        }
    }

    mock_response = MagicMock()
    mock_response.json.return_value = erp_response
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    result = await fetch_reservation_qr(
        erp_client=mock_client,
        ticket_id="TKT-2026-03-00067",
    )

    assert isinstance(result, ReservationQrData)
    assert result.ticket_id == "TKT-2026-03-00067"
    assert result.token == "a6fC8IHgNohlJnuaCDr8iTuyFRmfqZeE"
    assert result.qr_token_id == "a6fC8IHgNohlJnuaCDr8iTuyFRmfqZeE"
    assert result.qr_image_url == "/files/qr-TKT-2026-03-00067aec8dc.png"
    assert result.status == "ACTIVE"
    assert result.expires_at is None
    assert result.is_new is False
    mock_client.post.assert_called_once_with(
        f"{ERP_BASE_PATH}.qr_controller.get_qr_for_reservation",
        json={"reservation_id": "TKT-2026-03-00067"},
        timeout=15.0,
    )


# uv run pytest -s chatbot/api/tests/test_qr_utils.py::test_fetch_qr_upload_and_send_whatsapp
@pytest.mark.asyncio
async def test_fetch_qr_upload_and_send_whatsapp() -> None:
    """Recupera el QR del ticket TKT-2026-04-00136, lo sube a WhatsApp Media API y lo envía."""
    ticket_id = "TKT-2026-04-00136"
    recipient = "+34936069261"

    erp_client = build_erp_client()
    try:
        qr_data = await fetch_reservation_qr(erp_client=erp_client, ticket_id=ticket_id)
    finally:
        await erp_client.aclose()

    print(f"QR data: ticket={qr_data.ticket_id}, image_url={qr_data.qr_image_url}")
    assert qr_data.ticket_id == ticket_id

    image_url = build_qr_image_url(qr_data.qr_image_url)
    print(f"Image URL: {image_url}")

    manager = WhatsAppManager(request_timeout=60.0)
    safe_phone = recipient.replace("+", "").replace(" ", "")
    save_path = create_or_retrieve_images_dir() / f"{safe_phone}.png"
    media_id = await manager.upload_media(
        image_url=image_url, mime_type="image/png", save_path=save_path
    )
    print(f"media_id={media_id}")
    print(f"Imagen guardada en: {save_path}")
    assert media_id, "Se esperaba un media_id no vacío"
    assert save_path.exists(), f"Se esperaba que la imagen se guardara en {save_path}"

    caption = build_qr_caption(ticket_id=qr_data.ticket_id, token=qr_data.token)
    sent = await manager.send_image_by_id(
        to=recipient, image_id=media_id, caption=caption
    )
    print(f"Mensaje enviado: {sent}")
    assert sent, "Se esperaba que el mensaje fuera enviado correctamente"


# uv run pytest -s chatbot/api/tests/test_qr_utils.py::test_upload_local_image_to_meta
@pytest.mark.asyncio
async def test_upload_local_image_to_meta() -> None:
    """Sube static/images/34936069261.png a Meta Media API e imprime el media_id."""
    image_path = (
        Path(__file__).resolve().parents[3] / "static" / "images" / "34936069261.png"
    )
    assert image_path.exists(), f"Imagen no encontrada: {image_path}"

    image_bytes = image_path.read_bytes()
    print(f"Imagen: {image_path.name} ({len(image_bytes)} bytes)")

    manager = WhatsAppManager(request_timeout=60.0)
    media_id = await manager.upload_media_bytes(
        image_bytes=image_bytes,
        content_type="image/png",
        filename=image_path.name,
    )

    print(f"media_id={media_id}")
    assert media_id, "Se esperaba un media_id no vacío"


@pytest.mark.asyncio
async def test_send_image_by_media_id() -> None:
    """Envía una imagen a WhatsApp usando un media_id ya existente en Meta."""
    recipient = "+34 936069261"
    media_id = "1674096743733255"

    manager = WhatsAppManager(request_timeout=60.0)
    sent = await manager.send_image_by_id(to=recipient, image_id=media_id)
    print(f"Mensaje enviado: {sent}")
    assert sent, "Se esperaba que el mensaje fuera enviado correctamente"

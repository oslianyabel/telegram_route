# uv run pytest -s .\chatbot\messaging\tests\test_whatsapp.py
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from chatbot.messaging.whatsapp import WhatsAppManager

TEST_PHONE = "+1 835 235 3226"
TEST_MSG = "Mensaje de prueba"
TEST_IMAGE_URL = "https://erpnext-198181-0.cloudclusters.net/files/menu3761b2.png"
TEST_DOC_URL = "https://erpnext-198181-0.cloudclusters.net/files/productos.pdf"
TEST_MESSAGE_ID = ""


@pytest.fixture
def whatsapp_manager() -> WhatsAppManager:
    return WhatsAppManager()


@pytest.mark.asyncio
async def test_send_text(whatsapp_manager: WhatsAppManager):
    print("=" * 25 + "test_send_text" + "=" * 25)
    assert await whatsapp_manager.send_text(TEST_PHONE, TEST_MSG)


@pytest.mark.asyncio
async def test_send_image(whatsapp_manager: WhatsAppManager):
    print("=" * 25 + "test_send_image" + "=" * 25)
    assert await whatsapp_manager.send_image(
        to=TEST_PHONE, image_url=TEST_IMAGE_URL, caption="Menu semanal"
    )


@pytest.mark.asyncio
async def test_send_document(whatsapp_manager: WhatsAppManager):
    print("=" * 25 + "test_send_document" + "=" * 25)
    assert await whatsapp_manager.send_document_by_url(
        to=TEST_PHONE,
        doc_url=TEST_DOC_URL,
        filename="catalogo.pdf",
        caption="Catalogo de productos",
    )


# uv run pytest -s chatbot/messaging/tests/test_whatsapp.py::test_upload_media_bytes_returns_media_id
@pytest.mark.asyncio
async def test_upload_media_bytes_returns_media_id():
    print("=" * 25 + "test_upload_media_bytes_returns_media_id" + "=" * 25)
    image_path = (
        Path(__file__).resolve().parents[3] / "static" / "images" / "comprobante2.png"
    )
    image_bytes = image_path.read_bytes()
    print(f"Imagen: {image_path.name} ({len(image_bytes)} bytes)")

    manager = WhatsAppManager(request_timeout=60.0)
    media_id = await manager.upload_media_bytes(
        image_bytes=image_bytes,
        content_type="image/png",
        filename="comprobante2.png",
    )

    print(f"media_id={media_id}")
    assert media_id, "Se esperaba un media_id no vacío"

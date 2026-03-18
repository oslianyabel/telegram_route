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

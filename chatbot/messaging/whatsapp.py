import io
import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from chatbot.core.config import config

logger = logging.getLogger(__name__)
API_BASE = "https://graph.facebook.com/v23.0"
HTTP_OK = 200
HTTP_NO_CONTENT = 204
HTTP_SUCCESS_RANGE_START = 200
HTTP_SUCCESS_RANGE_END = 300
HTTP_CLIENT_ERROR = 400
WORDS_LIMIT = 1500


def dev_mock(func):
    async def wrapper(*args, **kwargs):
        if config.ENV_STATE == "dev":
            return True
        return await func(*args, **kwargs)

    return wrapper


def _ensure_rgb_png(image_bytes: bytes) -> bytes:
    """Convierte la imagen a RGB y la recodifica como PNG si es necesario.

    WhatsApp requiere imágenes en modo RGB o RGBA. Los PNG en modo 1-bit (blanco/negro)
    o en escala de grises (L) no se renderizan correctamente en algunos clientes.
    """
    with Image.open(io.BytesIO(image_bytes)) as img:
        if img.mode in ("RGB", "RGBA"):
            return image_bytes
        rgb_img = img.convert("RGB")
        buf = io.BytesIO()
        rgb_img.save(buf, format="PNG")
        return buf.getvalue()


class WhatsAppClient:
    def __init__(
        self,
        request_timeout: float = 15.0,
    ):
        self.access_token = config.WHATSAPP_ACCESS_TOKEN
        self.phone_number_id = config.WHATSAPP_PHONE_NUMBER_ID
        self.request_timeout = request_timeout

        if not self.access_token or not self.phone_number_id:
            logger.error(
                "WhatsAppManager initialized without credentials; API calls will fail."
            )

    @property
    def messages_url(self) -> str:
        return f"{API_BASE}/{self.phone_number_id}/messages"

    @property
    def headers_get(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def headers_post(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def check_response_status(self, resp: requests.Response | httpx.Response) -> bool:
        if resp.status_code >= HTTP_CLIENT_ERROR:
            try:
                logger.error(
                    f"WhatsApp API error {resp.status_code}: {resp.text[:500]}"
                )
            except Exception:
                logger.error(f"WhatsApp API error {resp.status_code}")

        return resp.status_code < HTTP_CLIENT_ERROR

    def _post(self, payload: dict[str, Any]) -> bool:
        logger.debug(f"WhatsApp API request payload: {payload}")
        resp = requests.post(
            self.messages_url,
            headers=self.headers_post,
            data=json.dumps(payload),
            timeout=self.request_timeout,
        )
        return self.check_response_status(resp)

    def _get(
        self, url: str, params: dict[str, Any] | None = None, stream: bool = False
    ) -> bool:
        logger.debug(f"WhatsApp API GET: {url} params={params}")
        resp = requests.get(
            url,
            headers=self.headers_get,
            params=params,
            timeout=self.request_timeout,
            stream=stream,
        )
        return self.check_response_status(resp)

    async def _apost(self, payload: dict[str, Any]) -> bool:
        timeout = httpx.Timeout(self.request_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                self.messages_url, headers=self.headers_post, json=payload
            )
            return self.check_response_status(resp)

    async def _aget(self, url: str) -> bool:
        timeout = httpx.Timeout(self.request_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=self.headers_get)
            return self.check_response_status(resp)


class WhatsAppManager(WhatsAppClient):
    @dev_mock
    async def send_text_chunk(
        self, to: str, body: str, message_id: str | None = None
    ) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        if message_id:
            payload["context"] = {"message_id": message_id}
        return await self._apost(payload)

    async def send_text(
        self, user_number: str, text: str, message_id: str | None = None
    ) -> bool:
        if len(text) <= WORDS_LIMIT:
            return await self.send_text_chunk(user_number, text, message_id)

        logger.info("Fragmentando respuesta")
        start = 0
        ok = True
        while start < len(text):
            end = min(start + WORDS_LIMIT, len(text))

            if end < len(text) and text[end] != "\n":
                newline_pos = text.rfind("\n", start, end)
                if newline_pos > start:
                    end = newline_pos

            chunk = text[start:end].strip()
            ok = ok and await self.send_text_chunk(user_number, chunk, message_id)
            start = end + 1 if text[end : end + 1] == "\n" else end

        return ok

    @dev_mock
    async def send_image_by_id(
        self,
        to: str,
        image_id: str,
        caption: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": {"id": image_id},
        }
        if message_id:
            payload["context"] = {"message_id": message_id}
        if caption:
            payload["image"]["caption"] = caption

        return await self._apost(payload)

    @dev_mock
    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "image",
            "image": {"link": image_url},
        }
        if message_id:
            payload["context"] = {"message_id": message_id}
        if caption:
            payload["image"]["caption"] = caption

        return await self._apost(payload)

    @dev_mock
    async def send_document(
        self,
        to: str,
        doc_dict: dict,
        message_id: str | None = None,
    ) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": doc_dict,
        }
        if message_id:
            payload["context"] = {"message_id": message_id}
        return await self._apost(payload)

    async def send_document_by_url(
        self,
        to: str,
        doc_url: str,
        filename: str | None = None,
        caption: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        doc_dict = {"link": doc_url}
        if filename:
            doc_dict["filename"] = filename
        if caption:
            doc_dict["caption"] = caption

        return await self.send_document(to=to, doc_dict=doc_dict, message_id=message_id)

    async def send_document_by_id(
        self,
        to: str,
        doc_id: str,
        filename: str | None = None,
        caption: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        doc_dict = {"id": doc_id}
        if filename:
            doc_dict["filename"] = filename
        if caption:
            doc_dict["caption"] = caption

        return await self.send_document(to=to, doc_dict=doc_dict, message_id=message_id)

    async def send_delivery_policy(
        self,
        to: str,
        message_id: str,
    ) -> None:
        await self.send_document_by_id(
            to=to,
            doc_id="1510082110074936",
            caption="politica de entregas y cancelaciones",
            filename="politica_apacha.pdf",
            message_id=message_id,
        )

    async def upload_media_bytes(
        self,
        image_bytes: bytes,
        content_type: str = "image/png",
        filename: str = "image.png",
        timeout: float = 60.0,
    ) -> str:
        """Sube bytes de imagen a la Media API de Meta y retorna el media_id."""
        upload_url = f"{API_BASE}/{self.phone_number_id}/media"
        headers = {"Authorization": f"Bearer {self.access_token}"}
        files = {
            "file": (filename, image_bytes, content_type),
            "messaging_product": (None, "whatsapp"),
            "type": (None, content_type),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
            upload_resp = await client.post(upload_url, headers=headers, files=files)
            upload_resp.raise_for_status()
            data = upload_resp.json()

        media_id: str = data["id"]
        return media_id

    async def upload_media(
        self,
        image_url: str,
        mime_type: str = "image/png",
        save_path: Path | None = None,
    ) -> str:
        """Descarga una imagen y la sube a la Media API de Meta.

        Retorna el media_id que luego puede usarse en send_image_by_id.
        Necesario cuando la URL de la imagen no es accesible públicamente
        por Meta (ej: URLs de un ERP en red interna).
        """
        timeout = httpx.Timeout(self.request_timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            download_resp = await client.get(image_url)
            download_resp.raise_for_status()
            image_bytes = download_resp.content
            content_type = download_resp.headers.get("content-type", mime_type)

        image_bytes = _ensure_rgb_png(image_bytes)
        content_type = "image/png"

        if save_path is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(image_bytes)
            logger.debug("[upload_media] Saved image to %s", save_path)

        return await self.upload_media_bytes(image_bytes, content_type)

    @dev_mock
    async def mark_read(self, message_id: str) -> bool:
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        return await self._apost(payload)

    @dev_mock
    async def send_typing_indicator(self, message_id: str) -> bool:
        if not message_id:
            logger.debug("No message_id provided for typing indicator; skipping")
            return False

        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        return await self._apost(payload)


whatsapp_manager = WhatsAppManager()


if __name__ == "__main__":
    import asyncio

    TEST_PHONE = "18352353226"

    asyncio.run(whatsapp_manager.send_text(TEST_PHONE, "Hola"))

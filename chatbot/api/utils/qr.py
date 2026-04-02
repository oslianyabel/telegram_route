from __future__ import annotations

import httpx

from chatbot.ai_agent.models import ERP_BASE_PATH, ReservationQrData
from chatbot.ai_agent.tools.erp_utils import extract_erp_data
from chatbot.core.config import config

ERP_TIMEOUT_SECONDS: float = 15.0


def build_qr_image_url(qr_image_url: str, erp_host: str | None = None) -> str:
    """Return the absolute ERP URL for a QR image path."""
    host = (erp_host or config.ERP_HOST).rstrip("/")
    image_path = qr_image_url.strip()
    if not image_path:
        raise ValueError("qr_image_url is empty")
    return f"{host}/{image_path.lstrip('/')}"


def build_qr_caption(ticket_id: str, token: str) -> str:
    """Return the message caption sent together with the QR image."""
    return f"Your check-in QR for ticket {ticket_id} is ready.\nToken: {token}"


async def fetch_reservation_qr(
    erp_client: httpx.AsyncClient,
    ticket_id: str,
    timeout: float = ERP_TIMEOUT_SECONDS,
) -> ReservationQrData:
    """Fetch the reservation QR token and image path from the ERP."""
    response = await erp_client.post(
        f"{ERP_BASE_PATH}.qr_controller.get_qr_for_reservation",
        json={"reservation_id": ticket_id},
        timeout=timeout,
    )
    response.raise_for_status()
    data = extract_erp_data(response.json())
    return ReservationQrData.model_validate(data)

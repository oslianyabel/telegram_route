# uv run pytest -s chatbot/api/tests/test_qr_utils.py

from unittest.mock import AsyncMock, MagicMock

import pytest

from chatbot.ai_agent.models import ERP_BASE_PATH, ReservationQrData
from chatbot.api.utils.qr import (
    build_qr_caption,
    build_qr_image_url,
    fetch_reservation_qr,
)


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

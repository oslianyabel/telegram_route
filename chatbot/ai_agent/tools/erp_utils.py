"""Shared ERP response-parsing utility.

Frappe wraps controller responses as::

    {"message": <controller_response>}

And our API controllers wrap their own output as::

    {"success": true, "message": <actual_data>}

So the full JSON looks like::

    {"message": {"success": true, "message": <actual_data>}}

``extract_erp_data`` unwraps both layers and returns ``<actual_data>``.
"""

from __future__ import annotations

from typing import Any


def extract_erp_data(response_json: dict[str, Any]) -> Any:
    """Unwrap the ERP response envelope and return the payload.

    Frappe wraps controller responses as::

        {"message": {"success": true, "message": "...", "data": <payload>, "meta": ...}}

    This helper extracts ``<payload>`` from the ``data`` key.

    Handles three shapes:

    1. ``{"message": {"data": <payload>, ...}}`` → ``<payload>``
    2. ``{"message": <payload>}`` (no ``data`` key) → ``<payload>``
    3. Flat dict (no ``message`` key at all) → ``response_json``
    """
    wrapper: Any = response_json.get("message", response_json)

    # Controller-level envelope: {"success": ..., "data": <payload>, ...}
    if isinstance(wrapper, dict) and "data" in wrapper:
        return wrapper["data"]

    return wrapper


def extract_erp_meta(response_json: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the ``meta`` pagination object from the ERP response, if present."""
    wrapper: Any = response_json.get("message", response_json)
    if isinstance(wrapper, dict):
        return wrapper.get("meta")
    return None


def extract_erp_error(response_json: dict[str, Any]) -> str:
    """Extract a human-readable error message from a failed ERP response body.

    Handles the ERP error envelope::

        {"message": {"success": false, "error": {"code": "...", "message": "..."}}}
    """
    wrapper: Any = response_json.get("message", response_json)
    if isinstance(wrapper, dict):
        error = wrapper.get("error", {})
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or wrapper)
        return str(wrapper.get("message") or wrapper)
    return str(wrapper)

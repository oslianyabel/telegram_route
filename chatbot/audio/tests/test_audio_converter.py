# uv run pytest -s chatbot/audio/tests/test_audio_converter.py::test_convert_ogg_to_mp3
"""Tests for convert_ogg_to_mp3.

Requires static/test.ogg to exist in the project root.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chatbot.audio.audio_converter import convert_ogg_to_mp3

ffmpeg_available = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg no está instalado o no está en el PATH",
)

STATIC_DIR = Path(__file__).resolve().parents[3] / "static/voice"
INPUT_FILE = STATIC_DIR / "test.ogg"
OUTPUT_FILE = STATIC_DIR / "test_converted.mp3"


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def cleanup_output() -> None:  # type: ignore[return]
    """Remove the converted file after each test run."""
    yield
    if OUTPUT_FILE.exists():
        OUTPUT_FILE.unlink()


@ffmpeg_available
@pytest.mark.anyio
async def test_convert_ogg_to_mp3() -> None:
    if not INPUT_FILE.exists():
        pytest.skip(f"Test file not found: {INPUT_FILE}")

    result = await convert_ogg_to_mp3(input_path=INPUT_FILE, output_path=OUTPUT_FILE)

    print(f"\nInput : {INPUT_FILE}")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Result: {result}")

    assert result is True, "convert_ogg_to_mp3 returned False"
    assert OUTPUT_FILE.exists(), "Output file was not created"
    assert OUTPUT_FILE.stat().st_size > 0, "Output file is empty"

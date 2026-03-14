import logging
import sys
from pathlib import Path

from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from chatbot.core.config import config

logger = logging.getLogger(__name__)
REQUEST_TIMEOUT_SECONDS: float = 30.0
openai_client = OpenAI(api_key=config.OPENAI_API_KEY, timeout=REQUEST_TIMEOUT_SECONDS)
AVAILABLE_AUDIO_FORMATS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}


def transcribe_audio(voice_path: str) -> str:
    if not any(voice_path.endswith(ext) for ext in AVAILABLE_AUDIO_FORMATS):
        logger.warning(
            f"Audio format of {voice_path} may not be supported for transcription"
        )
    with open(voice_path, "rb") as audio_file:
        transcription = openai_client.audio.transcriptions.create(
            model="gpt-4o-transcribe", file=audio_file
        )
        logger.debug(f"Transcription of {voice_path}: {transcription.text}")
        return transcription.text


if __name__ == "__main__":
    voice_path = (
        "C:/Users/lilia/Desktop/Projects/DeepZide/apacha_bot/static/voice/test.ogg"  # noqa: E501
    )
    print(transcribe_audio(voice_path))

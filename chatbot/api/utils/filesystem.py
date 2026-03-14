import logging
import os

logger = logging.getLogger(__name__)


def create_dirs() -> None:
    directories = ["static/images", "static/documents", "static/voice"]

    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        if not os.path.exists(directory):
            logger.error(f"Failed to create directory: {directory}")

    logger.info(f"Directories {directories} are ready")

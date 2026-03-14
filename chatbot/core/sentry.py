import logging

import sentry_sdk

from chatbot.core.config import config

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        enable_logs=False,
    )
    logger.info("Sentry initialized")

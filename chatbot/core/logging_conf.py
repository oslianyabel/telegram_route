import os

from chatbot.core.config import config
from logging.config import dictConfig

if not os.path.exists("logs"):
    os.makedirs("logs")
    print("Directory 'logs' created")

logging_conf = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "correlation_id": {
            "()": "asgi_correlation_id.CorrelationIdFilter",
            "uuid_length": 8,
            "default_value": "-",
        }
    },
    "formatters": {
        "console": {
            "class": "logging.Formatter",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
            "format": "(%(correlation_id)s) %(name)s:%(lineno)d - %(message)s",
        },
        "file": {
            "class": "logging.Formatter",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
            "format": "%(asctime)s | %(levelname)-8s | [%(correlation_id)s] %(name)s:%(lineno)d - %(message)s",
        },
        "file_json": {
            "class": "pythonjsonlogger.json.JsonFormatter",
            "datefmt": "%Y-%m-%dT%H:%M:%S",
            "format": "%(asctime)s %(levelname)-8s %(correlation_id)s %(name)s %(lineno)d %(message)s",
        },
    },
    "handlers": {
        "default": {
            "class": "rich.logging.RichHandler",
            "level": "DEBUG",
            "formatter": "console",
            "filters": ["correlation_id"],
        },
        "rotating_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "file_json",
            "filename": "logs/chatbot.log",
            "maxBytes": 1024 * 1024,  # 1MB
            "backupCount": 5,
            "encoding": "utf8",
            "filters": ["correlation_id"],
        },
        "sentry": {
            "class": "sentry_sdk.integrations.logging.SentryHandler",
            "level": "INFO",
            "formatter": "file",
            "filters": ["correlation_id"],
        },
    },
    "loggers": {
        "uvicorn": {
            "handlers": ["default", "rotating_file", "sentry"],
            "level": "INFO",
        },
        "databases": {
            "handlers": ["default", "rotating_file"],
            "level": "WARNING",
        },
        "chatbot": {
            "handlers": ["default", "rotating_file", "sentry"],
            "level": "DEBUG" if config.ENV_STATE == "dev" else "INFO",
            "propagate": False,
        },
    },
}


def init_logging():
    """Initialize logging configuration."""
    dictConfig(logging_conf)

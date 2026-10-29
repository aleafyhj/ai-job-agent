import json
import logging
import logging.config
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("request_id", "method", "route", "status_code", "duration_ms"):
            if hasattr(record, field):
                data[field] = getattr(record, field)
        # Do not serialize arbitrary exception text: it may contain secrets.
        if record.exc_info:
            data["exception_type"] = record.exc_info[0].__name__
        return json.dumps(data, ensure_ascii=False)


def configure_logging(level: str) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["console"], "level": level},
            "loggers": {
                "uvicorn": {"handlers": [], "propagate": True},
                "uvicorn.error": {"handlers": [], "propagate": True},
                "uvicorn.access": {"handlers": [], "propagate": False},
                "httpx": {"level": "WARNING", "handlers": [], "propagate": True},
                "httpx2": {"level": "WARNING", "handlers": [], "propagate": True},
                "httpcore": {"level": "WARNING", "handlers": [], "propagate": True},
            },
        }
    )

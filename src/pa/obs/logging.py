import json
import logging

import structlog

from pa.obs.redact import redact


def redact_processor(logger, method_name, event_dict):
    return redact(event_dict)


def configure() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_processor,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ]
    )


def configure_sdk_logging():
    from lark_oapi.core.log import logger

    class SafeFormatter(logging.Formatter):
        def format(self, record):
            return json.dumps(
                {"sdk": "lark", "level": record.levelname, "message": redact(record.getMessage())},
                ensure_ascii=False,
            )

    for handler in logger.handlers:
        handler.setFormatter(SafeFormatter())

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

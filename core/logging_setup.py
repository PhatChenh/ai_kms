import structlog
from structlog.contextvars import bind_contextvars


def setup_logger():
    structlog.configure(processors=[
        structlog.processors.JSONRenderer()
    ])

    bind_contextvars(correlation_id=)

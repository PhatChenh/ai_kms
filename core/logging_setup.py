"""
core/logging_setup.py
─────────────────────
Single entry point for the entire logging system.

Usage (called once at startup in cli/main.py or scheduler/runner.py):
    from core.logging_setup import setup_logging
    setup_logging(log_level="DEBUG", dev_mode=True)

Usage (every other file — never import setup_logging outside of startup):
    import structlog
    logger = structlog.get_logger(__name__)

Usage (start of each pipeline run — stamps every log line in this run):
    from core.logging_setup import new_correlation_id
    correlation_id = new_correlation_id()   # stores in contextvars automatically

Design decisions:
    - structlog wraps stdlib logging as the backend. This means third-party
      libraries that use stdlib logging (e.g. httpx, sqlalchemy) also appear
      in the same log file, with the same format.
    - Two outputs: JSON to file (machine-readable, for the daily briefing to
      parse), pretty-print to console (human-readable, for dev mode).
    - correlation_id lives in contextvars, NOT as a function argument. Every
      log line in a pipeline run inherits it automatically.
"""

import logging
import logging.handlers
import sys
import uuid
from pathlib import Path

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars


# ─── Public API ──────────────────────────────────────────────────────────────

def setup_logging(log_level: str = "INFO", dev_mode: bool = False) -> None:
    """
    Configure structlog + stdlib logging. Call this exactly once at startup.

    Args:
        log_level:  One of DEBUG / INFO / WARNING / ERROR / CRITICAL.
                    In step 4, this will be wired to config.yaml instead.
        dev_mode:   True = pretty-print colours to console.
                    False = JSON only (production / CI).
    """
    _ensure_log_dir()
    _configure_structlog()
    _configure_stdlib_handlers(log_level=log_level, dev_mode=dev_mode)


def new_correlation_id() -> str:
    """
    Generate a fresh correlation_id and bind it into the current context.

    Call this at the very start of each pipeline run:
        correlation_id = new_correlation_id()

    Every log line emitted anywhere during this run will automatically carry
    the same correlation_id field — no argument passing required.

    Returns:
        The generated UUID string (useful if you want to store it in the
        audit trail or the note's frontmatter).
    """
    # Always clear first — contextvars are inherited by async tasks, so a
    # leftover correlation_id from a previous run could silently bleed through.
    clear_contextvars()

    correlation_id = str(uuid.uuid4())
    bind_contextvars(correlation_id=correlation_id)   # ← THIS is what was blank
    return correlation_id


# ─── Internal helpers ─────────────────────────────────────────────────────────

_LOG_DIR = Path(__file__).parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "kms.log"


def _ensure_log_dir() -> None:
    """Create logs/ if it doesn't exist. Git-ignored, so it won't be committed."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)


def _configure_structlog() -> None:
    """
    Build the structlog processor chain.

    Processors run in order on every log call. Think of them as middleware:
    each one receives the event dict, does something to it, and passes it on.

    The final processor must be ProcessorFormatter.wrap_for_formatter — this
    hands the event dict to stdlib logging, which then routes to our handlers.
    """
    shared_processors = [
        # 1. Pull correlation_id (and any other bound vars) into the event dict.
        #    This is what makes correlation_id appear on every line automatically.
        structlog.contextvars.merge_contextvars,

        # 2. Add the logger name (i.e. the __name__ of the calling module).
        structlog.stdlib.add_logger_name,

        # 3. Add log level string ("info", "warning", etc.).
        structlog.stdlib.add_log_level,

        # 4. Add ISO-8601 timestamp. fmt="iso" → "2026-05-01T14:23:01.123456Z"
        structlog.processors.TimeStamper(fmt="iso", utc=True),

        # 5. If an exception was passed via exc_info=True, format the traceback
        #    into the event dict so it survives JSON serialisation.
        structlog.processors.format_exc_info,

        # 6. Hand the event dict to stdlib logging for routing to handlers.
        #    This must be the last processor in the shared chain.
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]

    structlog.configure(
        processors=shared_processors,
        # BoundLogger gives us .info() / .warning() / .error() etc.
        wrapper_class=structlog.stdlib.BoundLogger,
        # stdlib LoggerFactory so structlog.get_logger(__name__) wires up
        # to Python's logging.getLogger(__name__) under the hood.
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Cache the logger after the first call — small perf win in hot paths.
        cache_logger_on_first_use=True,
    )


def _configure_stdlib_handlers(log_level: str, dev_mode: bool) -> None:
    """
    Attach handlers to the root stdlib logger.

    Handler 1 — RotatingFileHandler → JSON formatter (always on)
        Writes machine-readable JSON to logs/kms.log. The daily briefing
        pipeline reads this file to build the classification report.
        Rotates at 10 MB, keeps 5 backups so logs/ never fills a disk.

    Handler 2 — StreamHandler → ConsoleRenderer (dev_mode only)
        Human-readable, coloured output to stdout. Never enabled in
        production — JSON to file is the source of truth.
    """
    # Map the string level to a stdlib int constant (e.g. "INFO" → 20).
    if log_level.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        numeric_level = getattr(logging, log_level.upper())
    else:
        raise ValueError("Unknown log level")

    # ── JSON file handler ────────────────────────────────────────────────────
    json_formatter = structlog.stdlib.ProcessorFormatter(
        # The foreign_pre_chain processes log records from stdlib loggers
        # (third-party libs) so they also get our shared fields injected.
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
        ],
        # The final renderer: turns the event dict into a JSON string.
        processor=structlog.processors.JSONRenderer(),
    )

    file_handler = logging.handlers.RotatingFileHandler(
        filename=_LOG_FILE,
        maxBytes=10 * 1024 * 1024,   # 10 MB per file
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(json_formatter)
    file_handler.setLevel(numeric_level)

    handlers: list[logging.Handler] = [file_handler]

    # ── Console handler (dev mode only) ─────────────────────────────────────
    if dev_mode:
        console_formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.contextvars.merge_contextvars,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
            ],
            # ConsoleRenderer adds colours and aligns columns for readability.
            processor=structlog.dev.ConsoleRenderer(colors=True),
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(numeric_level)
        handlers.append(console_handler)

    # ── Wire everything into the root logger ─────────────────────────────────
    # Setting the root logger means ALL loggers (including third-party ones)
    # flow through our handlers. force=True removes any handlers that Python
    # may have added automatically before setup_logging() was called.
    logging.basicConfig(
        level=numeric_level,
        handlers=handlers,
        force=True,
    )
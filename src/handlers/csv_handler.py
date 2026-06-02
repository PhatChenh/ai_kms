"""CSV handler — extracts tabular text from .csv files via stdlib csv module.

Empty CSV files (no rows) return Success with text="" — consistent with
DocxHandler behaviour. Empty text is valid input for the LLM summarise stage,
which handles the no-content case gracefully.

Files larger than CONFIG.main.handlers.max_file_size_bytes are rejected before
any parse work to prevent a single large drop from OOMing the capture process.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from core.result import Failure, Result, Success
from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry

logger = structlog.get_logger(__name__)


@HandlerRegistry.register
class CsvHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".csv"

    def extract(self, path: Path) -> Result[RawContent]:
        # Lazy import — see handlers/pdf_handler.py for rationale.
        from core.config import CONFIG

        max_bytes = CONFIG.main.handlers.max_file_size_bytes

        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("csv.stat.failed", path=str(path), error=str(exc))
            return Failure(
                error=f"CSV stat failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )

        if size > max_bytes:
            logger.warning(
                "csv.too_large", path=str(path), size=size, limit=max_bytes
            )
            return Failure(
                error=f"CSV too large: {size} > {max_bytes} bytes",
                recoverable=False,
                context={"path": str(path), "size": size, "limit": max_bytes},
            )

        try:
            import csv

            lines: list[str] = []
            with path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                for row in reader:
                    lines.append(",".join(row))
            text = "\n".join(lines).strip()
        except Exception as exc:
            return Failure(
                error=f"CSV read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))

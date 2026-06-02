"""MSG handler — extracts headers + body from Outlook .msg files via extract-msg.

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
class MsgHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".msg"

    def extract(self, path: Path) -> Result[RawContent]:
        # Lazy import — see handlers/pdf_handler.py for rationale.
        from core.config import CONFIG

        max_bytes = CONFIG.main.handlers.max_file_size_bytes

        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("msg.stat.failed", path=str(path), error=str(exc))
            return Failure(
                error=f"MSG stat failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )

        if size > max_bytes:
            logger.warning(
                "msg.too_large", path=str(path), size=size, limit=max_bytes
            )
            return Failure(
                error=f"MSG too large: {size} > {max_bytes} bytes",
                recoverable=False,
                context={"path": str(path), "size": size, "limit": max_bytes},
            )

        try:
            import extract_msg

            with extract_msg.Message(str(path)) as msg:
                headers = "\n".join(
                    [
                        f"From: {msg.sender or ''}",
                        f"To: {msg.to or ''}",
                        f"Subject: {msg.subject or ''}",
                        f"Date: {msg.date or ''}",
                    ]
                )
                body = (msg.body or "").strip()
                text = f"{headers}\n\n{body}".strip()
        except Exception as exc:
            return Failure(
                error=f"MSG read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))

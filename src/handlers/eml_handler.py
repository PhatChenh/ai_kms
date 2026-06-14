"""EML handler — extracts headers + plain-text body from RFC 2822 email files.

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
class EmlHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".eml"

    def extract(self, path: Path, *, max_file_size_bytes: int | None = None) -> Result[RawContent]:
        if max_file_size_bytes is None:
            # Lazy import — see handlers/pdf_handler.py for rationale.
            from core.config import CONFIG

            max_bytes = CONFIG.main.handlers.max_file_size_bytes
        else:
            max_bytes = max_file_size_bytes

        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.warning("eml.stat.failed", path=str(path), error=str(exc))
            return Failure(
                error=f"EML stat failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )

        if size > max_bytes:
            logger.warning(
                "eml.too_large", path=str(path), size=size, limit=max_bytes
            )
            return Failure(
                error=f"EML too large: {size} > {max_bytes} bytes",
                recoverable=False,
                context={"path": str(path), "size": size, "limit": max_bytes},
            )

        try:
            import email
            from email import policy as email_policy

            raw = path.read_bytes()
            msg = email.message_from_bytes(raw, policy=email_policy.default)
            headers = "\n".join(
                [
                    f"From: {msg.get('From', '')}",
                    f"To: {msg.get('To', '')}",
                    f"Subject: {msg.get('Subject', '')}",
                    f"Date: {msg.get('Date', '')}",
                ]
            )
            body_parts: list[str] = []
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body_parts.append(part.get_content())
            else:
                if msg.get_content_type() == "text/plain":
                    body_parts.append(msg.get_content())
            body = "\n".join(body_parts).strip()
            text = f"{headers}\n\n{body}".strip()
        except Exception as exc:
            return Failure(
                error=f"EML read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))

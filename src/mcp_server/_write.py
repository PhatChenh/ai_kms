"""kms_write backing — save a chat insight as a new document."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from core.result import Failure, Result, Success


def _slugify(text: str) -> str:
    """Simple slug: lowercase, replace non-alphanumeric with hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


async def write_from_chat(
    content: str,
    title_hint: str | None = None,
    *,
    classify_queue=None,
    db_path: Path | None = None,
) -> Result[int]:
    """Save a chat insight as a new document in the knowledge system.

    Generates a ``chat/YYYYMMDD-HHMMSS-<slug>.md`` vault path, computes
    the content hash, and delegates to ``capture_upload``.  If a classify
    queue is available the new document id is enqueued best-effort.
    """
    from mcp_server.api import capture_upload  # noqa: C0415

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = _slugify(title_hint or "insight")
    vault_path = f"chat/{ts}-{slug}.md"
    content_hash = hashlib.sha256(content.encode()).hexdigest()

    result = await capture_upload(
        vault_path=vault_path,
        extracted_text=content,
        content_hash=content_hash,
        db_path=db_path,
    )

    if isinstance(result, Failure):
        return result

    doc_id = result.value

    # Enqueue for classify (best-effort)
    if classify_queue is not None:
        try:
            classify_queue.put_nowait(doc_id)
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "classify_queue.put_nowait failed doc_id=%s", doc_id
            )

    return Success(doc_id)

"""mcp_server/_resolve.py — Three-Tier DB Resolver

DB-first resolver with three modes:
  "summary" — row.summary (always available)
  "text"    — row.full_body with fallback to summary
  "file"    — row.vault_path (for laptop access)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.result import Failure, Result, Success
from storage.documents import get_by_id


@dataclass(frozen=True)
class ResolveResult:
    doc_id: int
    mode: str  # "summary" | "text" | "file"
    content: str
    title: str
    degraded: bool  # True if text mode fell back to summary


def resolve(
    doc_ids: list[int],
    mode: str = "summary",
    *,
    max_text_refs: int = 5,
    db_path: Path | None = None,
) -> Result[list[ResolveResult]]:
    """DB-first three-tier resolver.

    For each doc_id, fetch the document row via get_by_id() and return a
    ResolveResult based on the requested mode.

    Args:
        doc_ids:       List of document IDs to resolve in order.
        mode:          One of "summary", "text", or "file".
        max_text_refs: Max number of full_body results in text mode before
                       degrading to summary.
        db_path:       Optional override database path.

    Returns:
        Success([ResolveResult, ...]) with one entry per found document.
        Missing documents are silently skipped.
        Failure when get_by_id returns a Failure.
    """
    results: list[ResolveResult] = []

    for idx, doc_id in enumerate(doc_ids):
        match get_by_id(doc_id, db_path=db_path):
            case Success(None):
                # Missing document — skip, not an error
                continue
            case Success(row):
                title = row.title
                if mode == "summary":
                    content = row.summary or "[Summary pending]"
                    degraded = False
                elif mode == "text":
                    if idx < max_text_refs and row.full_body is not None:
                        content = row.full_body
                        degraded = False
                    else:
                        content = row.summary or "[Summary pending]"
                        degraded = True
                elif mode == "file":
                    content = row.vault_path
                    degraded = False
                else:
                    return Failure(
                        error=f"Unknown resolve mode: {mode!r}",
                        recoverable=False,
                        context={"mode": mode},
                    )
                results.append(
                    ResolveResult(
                        doc_id=doc_id,
                        mode=mode,
                        content=content,
                        title=title,
                        degraded=degraded,
                    )
                )
            case Failure() as f:
                return f

    return Success(results)


def resolve_dicts(
    doc_ids: list[int],
    mode: str = "summary",
    *,
    max_text_refs: int | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Convenience wrapper: resolve() → list[dict] for the MCP shim layer.

    Keeps tools.py C-14 clean by moving the dict-conversion loop here.
    Reads ``max_text_refs`` from CONFIG when not explicitly provided.
    """
    if max_text_refs is None:
        from core.config import CONFIG

        max_text_refs = CONFIG.main.mcp.inspect.max_text_refs

    return [
        {
            "doc_id": r.doc_id,
            "mode": r.mode,
            "content": r.content,
            "title": r.title,
            "degraded": r.degraded,
        }
        for r in resolve(
            doc_ids, mode, max_text_refs=max_text_refs, db_path=db_path
        ).unwrap()
    ]

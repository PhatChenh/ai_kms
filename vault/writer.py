"""
vault/writer.py

Atomic, idempotent vault writes with the updated_by_human safety gate.

Rules enforced here:
- updated_by_human=True + actor='ai' → Failure(recoverable=False). No retry fixes this.
- Atomic writes: tmp file in same dir → fsync → os.replace. No partial writes exposed.
- NFC normalisation on vault_path strings prevents ghost-duplicate SQLite rows on macOS.
- FS-only: no storage/ imports. Callers (pipelines) call storage.documents.upsert(outcome).

move_attachment is for non-md binaries only. .md notes always go through write_note /
move_note. Binaries carry no frontmatter and no updated_by_human gate.

See docs/plans/vault_layer.md Phase 4 for design diagrams and Option B merge rules.
TD-014 in STATE.md tracks the known limitation: callers cannot explicitly clear a known
field (e.g. reset tags to []) without a sentinel or NoteMetadataUpdate redesign.
"""

from __future__ import annotations

import hashlib
import os
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from core.result import Failure, Result, Success
from vault.frontmatter import NoteMetadata, dumps
from vault.reader import read_note, Note

WriteActor = Literal["ai", "human"]


@dataclass(frozen=True)
class WriteOutcome:
    """Returned by write_note and move_note; feed to storage.documents.upsert()."""

    vault_path: str       # POSIX, relative to vault root, NFC-normalised
    absolute_path: Path
    content_hash: str
    metadata: NoteMetadata


def _to_vault_path(absolute: Path) -> str:
    """Compute NFC-normalised POSIX vault_path relative to vault root."""
    from core.config import CONFIG
    rel = absolute.relative_to(CONFIG.main.vault.root).as_posix()
    return unicodedata.normalize("NFC", rel)


def _merge_metadata(
    incoming: NoteMetadata,
    existing: NoteMetadata | None,
    actor: WriteActor,
) -> NoteMetadata:
    """
    Merge caller metadata with existing note metadata (Option B rules).

    Option B: for each known field, use the caller's value if explicitly supplied
    (non-None for Optional fields, non-empty for list fields); otherwise fall back to
    the existing note's value. This prevents AI writes from silently clearing
    human-set fields when the pipeline only sets a subset of fields.

    Args:
        incoming: Metadata supplied by the caller.
        existing: Metadata from the note currently on disk (None for new notes).
        actor:    "ai" or "human" — controls updated_by_human stamp.

    Returns:
        Merged NoteMetadata ready to write.
    """
    ex = existing

    def _opt(new, old):
        """Return new if not None, else fall back to old."""
        return new if new is not None else old

    return NoteMetadata(
        # created: always preserve the earliest known date; seed with today for new notes
        created=(ex.created if ex else None) or incoming.created or date.today(),
        updated=datetime.now(timezone.utc),
        updated_by_human=(actor == "human"),
        type=_opt(incoming.type, ex.type if ex else None),
        # tags: keep existing if caller passes empty list (Option B for list fields)
        tags=incoming.tags if incoming.tags else (ex.tags if ex else []),
        project=_opt(incoming.project, ex.project if ex else None),
        domain=_opt(incoming.domain, ex.domain if ex else None),
        confidence=_opt(incoming.confidence, ex.confidence if ex else None),
        summary=_opt(incoming.summary, ex.summary if ex else None),
        source=_opt(incoming.source, ex.source if ex else None),
        source_file=_opt(incoming.source_file, ex.source_file if ex else None),
        status=_opt(incoming.status, ex.status if ex else None),
        # extra: keep existing unknown keys if caller didn't supply any
        extra=incoming.extra if incoming.extra else (ex.extra if ex else {}),
    )


def _atomic_write(path: Path, rendered: str) -> None:
    """
    Write rendered string to path atomically.

    Creates a dot-prefixed tmp file in the same directory (indexer skips dot-prefix
    files), fsyncs, then replaces atomically. Cleans up tmp on any failure.
    """
    tmp = path.parent / f".tmp_{uuid4().hex}.md"
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(rendered)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def write_note(
    path: Path,
    content: str,
    metadata: NoteMetadata,
    actor: WriteActor,
) -> Result[WriteOutcome]:
    """
    Write a note to disk atomically, respecting the updated_by_human safety gate.

    If the note already exists and updated_by_human=True, an AI actor is blocked
    (Failure, recoverable=False). A human actor always succeeds.

    Merges caller metadata with existing using Option B rules — see _merge_metadata.

    Args:
        path:     Absolute path to write to (parent must exist).
        content:  Note body text (no frontmatter block).
        metadata: Caller-supplied metadata.
        actor:    Who is writing: "ai" (pipeline) or "human" (CLI command).

    Returns:
        Success(WriteOutcome) or Failure(recoverable=False).
    """
    body = content.rstrip("\n")

    existing_note: Note | None = None
    if path.exists():
        match read_note(path):
            case Failure() as f:
                return f
            case Success(value=note):
                existing_note = note

        if existing_note.metadata.updated_by_human and actor == "ai":
            return Failure(
                error="note locked by human edit",
                recoverable=False,
                context={"path": str(path), "vault_path": _to_vault_path(path)},
            )

    merged = _merge_metadata(
        incoming=metadata,
        existing=existing_note.metadata if existing_note else None,
        actor=actor,
    )
    rendered = dumps(merged, body)

    try:
        _atomic_write(path, rendered)
    except Exception as exc:
        return Failure(
            error=f"write failed: {exc}",
            recoverable=False,
            context={"path": str(path)},
        )

    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return Success(
        WriteOutcome(
            vault_path=_to_vault_path(path),
            absolute_path=path,
            content_hash=content_hash,
            metadata=merged,
        )
    )


def move_note(
    src: Path,
    dst: Path,
    actor: WriteActor,
) -> Result[WriteOutcome]:
    """
    Move a note atomically from src to dst, respecting the updated_by_human gate.

    Same-filesystem: os.replace(src, dst) + atomic metadata rewrite on dst.
    Cross-filesystem (Errno 18 / EXDEV): write rendered content to tmp next to dst,
    fsync, os.replace(tmp, dst), then unlink src.

    Args:
        src:   Source path (must exist and be readable).
        dst:   Destination path (parent created if absent).
        actor: "ai" or "human".

    Returns:
        Success(WriteOutcome) with dst vault_path, or Failure(recoverable=False).
    """
    match read_note(src):
        case Failure() as f:
            return f
        case Success(value=current):
            pass

    if current.metadata.updated_by_human and actor == "ai":
        return Failure(
            error="note locked by human edit",
            recoverable=False,
            context={"src": str(src), "dst": str(dst)},
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    merged = _merge_metadata(
        incoming=current.metadata,
        existing=current.metadata,
        actor=actor,
    )
    rendered = dumps(merged, current.content)

    try:
        try:
            # Same-filesystem: atomic rename then rewrite merged metadata
            os.replace(src, dst)
            _atomic_write(dst, rendered)
        except OSError as exc:
            if exc.errno != 18:  # EXDEV = cross-device link
                raise
            # Cross-filesystem: write to tmp next to dst, then remove src
            tmp = dst.parent / f".tmp_{uuid4().hex}{dst.suffix}"
            try:
                with tmp.open("w", encoding="utf-8") as fh:
                    fh.write(rendered)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, dst)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise
            src.unlink()
    except Exception as exc:
        return Failure(
            error=f"move failed: {exc}",
            recoverable=False,
            context={"src": str(src), "dst": str(dst)},
        )

    content_hash = hashlib.sha256(current.content.encode("utf-8")).hexdigest()
    return Success(
        WriteOutcome(
            vault_path=_to_vault_path(dst),
            absolute_path=dst,
            content_hash=content_hash,
            metadata=merged,
        )
    )


def move_attachment(src: Path, dst: Path) -> Result[Path]:
    """
    Relocate a non-md binary file (PDF, DOCX, image) to dst atomically.

    No frontmatter is read, no updated_by_human gate applies — attachments are
    binary blobs, not vault notes. The caller picks dst and is responsible for
    NFC-normalising the path before storing it in the sibling .md note's
    source_file field.

    Never silently overwrites an existing dst — two PDFs can share a filename;
    the caller must pick a non-colliding name and retry.

    Args:
        src: Source path (must exist).
        dst: Destination path (parent created if absent, must NOT exist).

    Returns:
        Success(dst) or Failure(recoverable=False).
    """
    if not src.exists():
        return Failure(
            error="attachment source not found",
            recoverable=False,
            context={"src": str(src)},
        )

    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        return Failure(
            error="attachment already exists at dst",
            recoverable=False,
            context={"dst": str(dst)},
        )

    tmp: Path | None = None
    try:
        try:
            os.replace(src, dst)
        except OSError as exc:
            if exc.errno != 18:  # EXDEV = cross-device link
                raise
            # Cross-filesystem: copy bytes to tmp next to dst, then remove src.
            tmp = dst.parent / f".tmp_{uuid4().hex}{dst.suffix}"
            with tmp.open("wb") as fh:
                fh.write(src.read_bytes())
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, dst)
            src.unlink()
            tmp = None  # ownership transferred; skip cleanup
    except Exception as exc:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        return Failure(
            error=f"attachment move failed: {exc}",
            recoverable=False,
            context={"src": str(src), "dst": str(dst)},
        )

    return Success(dst)

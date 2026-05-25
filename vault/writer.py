"""
vault/writer.py

Atomic, idempotent vault writes with the updated_by_human safety gate.

Rules enforced here:
- updated_by_human=True + actor='ai' → Failure(recoverable=False). No retry fixes this.
- Atomic writes: tmp file in same dir → fsync → os.replace. No partial writes exposed.
- NFC normalisation on vault_path strings prevents ghost-duplicate SQLite rows on macOS.
- FS-only: no storage/ imports. Callers (pipelines) call storage.documents.upsert(outcome).
- Pipeline-level merge: write_note writes exactly what the caller passes. To preserve
  existing fields, callers must read_note first. Only `created` is preserved automatically
  as a factual timestamp invariant (see _merge_metadata).

move_attachment is for non-md binaries only. .md notes always go through write_note /
move_note. Binaries carry no frontmatter and no updated_by_human gate.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from core.result import Failure, Result, Success
from vault.frontmatter import NoteMetadata, dumps
from vault.paths import to_vault_path
from vault.reader import read_note, Note

WriteActor = Literal["ai", "human"]


@dataclass(frozen=True)
class WriteOutcome:
    """Returned by write_note and move_note; feed to storage.documents.upsert()."""

    vault_path: str       # POSIX, relative to vault root, NFC-normalised
    absolute_path: Path
    content_hash: str
    metadata: NoteMetadata


def _merge_metadata(
    incoming: NoteMetadata,
    existing: NoteMetadata | None,
    actor: WriteActor,
) -> NoteMetadata:
    """
    Apply invariant timestamps to caller-supplied metadata before writing.

    Pipeline-level merge contract: callers own all field decisions. To preserve
    existing field values, the caller must read_note first and pass those values
    explicitly. The only exception is `created` — once set it must never be lost,
    regardless of what the caller passes.

    Args:
        incoming: Metadata supplied by the caller (authoritative for all fields).
        existing: Metadata from the note currently on disk (None for new notes).
        actor:    "ai" or "human" — controls updated_by_human stamp.

    Returns:
        NoteMetadata with timestamps applied, ready to write.
    """
    ex = existing
    created = (ex.created if ex and ex.created else None) or incoming.created or date.today()
    return NoteMetadata(
        created=created,
        updated=datetime.now(timezone.utc),
        updated_by_human=(actor == "human"),
        type=incoming.type,
        tags=incoming.tags,
        project=incoming.project,
        domain=incoming.domain,
        confidence=incoming.confidence,
        summary=incoming.summary,
        source=incoming.source,
        source_file=incoming.source_file,
        attachment_path=incoming.attachment_path,
        status=incoming.status,
        extra=incoming.extra,
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
                context={"path": str(path), "vault_path": to_vault_path(path)},
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
            vault_path=to_vault_path(path),
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

    Writes merged content to dst atomically (tmp + fsync + os.replace), then
    unlinks src. dst is fully written before src is removed, so any failure
    leaves src intact and the move is safely retryable. Works across filesystems
    because the tmp file always lives in dst's own directory.

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
        # Write merged content to dst atomically, then drop src. dst is fully
        # written before src is unlinked, so a failure leaves src intact and the
        # caller can retry — no window where dst holds stale, un-merged metadata.
        _atomic_write(dst, rendered)
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
            vault_path=to_vault_path(dst),
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

"""
daemon/extractor.py

Text extraction + content hashing for the sync daemon.

Reads a file's raw bytes once, computes SHA-256 over those bytes, then tries
text extraction via the handler registry.  Falls back to binary content when
no handler claims the file or extraction fails.  The hash is always over raw
bytes, not extracted text (ADR-0013).

Usage:
    from daemon.extractor import extract, TextContent, BinaryContent

    match extract(path, vault_root, max_file_size_bytes=50_000_000):
        case Success(value=TextContent() as tc):
            upload_text(tc)
        case Success(value=BinaryContent() as bc):
            upload_binary(bc)
        case Failure() as f:
            logger.error("extract failed", **f.to_log_dict())
"""

from __future__ import annotations

import hashlib
import mimetypes
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from core.result import Failure, Result, Success

# ── output types ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TextContent:
    """Successfully extracted text content.

    Attributes:
        text:             The extracted plain text.
        content_hash:     SHA-256 hex digest of the **raw file bytes**.
        vault_path:       NFC-normalised POSIX path relative to vault_root.
        original_filename: The file's leaf name (``path.name``).
        file_size_bytes:  Size of the raw file in bytes.
    """

    text: str
    content_hash: str
    vault_path: str
    original_filename: str
    file_size_bytes: int


@dataclass(frozen=True)
class BinaryContent:
    """Binary content for fallback upload when text extraction is not possible.

    Attributes:
        raw_bytes:         The raw file bytes (for upload).
        content_hash:      SHA-256 hex digest of the raw file bytes.
        vault_path:        NFC-normalised POSIX path relative to vault_root.
        original_filename:  The file's leaf name (``path.name``).
        file_size_bytes:   Size of the raw file in bytes.
        mime_type:         Detected MIME type (e.g. ``"image/png"``).
    """

    raw_bytes: bytes
    content_hash: str
    vault_path: str
    original_filename: str
    file_size_bytes: int
    mime_type: str


# ── main entry point ─────────────────────────────────────────────────────────


def extract(
    path: Path,
    vault_root: Path,
    max_file_size_bytes: int,
) -> Result[TextContent | BinaryContent]:
    """Extract text content from a file, falling back to binary upload.

    1. Read raw bytes → compute SHA-256.
    2. Compute *vault_path* (NFC-normalised POSIX relative to *vault_root*).
    3. Try ``HandlerRegistry.resolve(path)`` → call ``handler.extract(...)``.
    4. If extraction succeeds, return ``TextContent``.
    5. If no handler or extraction fails, return ``BinaryContent``.

    Args:
        path:                Absolute path to the file on disk.
        vault_root:          Absolute path to the vault root directory.
        max_file_size_bytes: Passed through to handler.extract().

    Returns:
        ``Success(TextContent)`` on successful text extraction.
        ``Success(BinaryContent)`` when no handler found or extraction fails.
        ``Failure(recoverable=True)`` if the file cannot be read (vanished, etc.).
    """

    # ── 1. Check file size before reading ────────────────────────────────
    try:
        file_size_bytes = path.stat().st_size
    except OSError:
        return Failure(
            error=f"cannot read file: {path}",
            recoverable=True,
            context={"path": str(path)},
        )
    if file_size_bytes > max_file_size_bytes:
        return Failure(
            error=f"file too large: {file_size_bytes} > {max_file_size_bytes} bytes",
            recoverable=False,
            context={"path": str(path), "size": file_size_bytes},
        )

    # ── 2. Read raw bytes + hash ─────────────────────────────────────────
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return Failure(
            error=f"cannot read file: {path}",
            recoverable=True,
            context={"path": str(path)},
        )

    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    original_filename = path.name

    # ── 3. Compute vault_path ────────────────────────────────────────────
    try:
        rel = path.resolve().relative_to(vault_root.resolve())
    except ValueError:
        return Failure(
            error=f"path is not inside vault_root: {path}",
            recoverable=False,
            context={"path": str(path), "vault_root": str(vault_root)},
        )
    vault_path = unicodedata.normalize("NFC", rel.as_posix())

    # ── 3. Try handler registry dispatch ─────────────────────────────────
    from handlers.registry import HandlerRegistry

    match HandlerRegistry.resolve(path):
        case Success(value=handler):
            match handler.extract(path, max_file_size_bytes=max_file_size_bytes):
                case Success(value=raw_content):
                    return Success(
                        TextContent(
                            text=raw_content.text,
                            content_hash=content_hash,
                            vault_path=vault_path,
                            original_filename=original_filename,
                            file_size_bytes=file_size_bytes,
                        )
                    )
                case Failure():
                    pass  # fall through to binary fallback
        case Failure():
            pass  # fall through to binary fallback

    # ── 4. Binary fallback ───────────────────────────────────────────────
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"

    return Success(
        BinaryContent(
            raw_bytes=raw_bytes,
            content_hash=content_hash,
            vault_path=vault_path,
            original_filename=original_filename,
            file_size_bytes=file_size_bytes,
            mime_type=mime_type,
        )
    )

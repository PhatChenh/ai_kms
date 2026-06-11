"""mcp_server/_resolve.py — Binary Resolver Helper (kms_inspect backing)

Given either a summary note's path or a binary's own path, find the real binary
and return its raw extracted text — no AI, no re-summarising.

Public API:
    inspect(path: Path) -> Result[str]
"""

from __future__ import annotations

from pathlib import Path

from core.config import CONFIG
from core.result import Failure, Result, Success
from handlers.registry import HandlerRegistry
from vault.reader import read_note


def inspect(path: Path) -> Result[str]:
    """Return raw text from a binary, resolving via attachment_path if needed.

    Args:
        path: Either a sibling .md with attachment_path frontmatter, or the
              binary file itself.

    Returns:
        Success(raw_text) — the extracted plain text.
        Failure(recoverable=False) — .md without attachment_path, unreadable
        binary, no matching handler, or any extractor failure.
    """
    # If the path is a .md file, try to resolve via attachment_path frontmatter
    if path.suffix.lower() == ".md":
        match read_note(path):
            case Success(note):
                if note.metadata.attachment_path:
                    binary_path = CONFIG.main.vault.root / note.metadata.attachment_path
                else:
                    return Failure(
                        error="Not a binary resolver target — no attachment_path "
                        "in frontmatter",
                        recoverable=False,
                        context={"path": str(path)},
                    )
            case Failure() as f:
                return f
    else:
        binary_path = path

    # Resolve handler and extract text
    match HandlerRegistry.resolve(binary_path):
        case Success(handler):
            match handler.extract(binary_path):
                case Success(raw):
                    return Success(raw.text)
                case Failure() as f:
                    return f
        case Failure() as f:
            return f

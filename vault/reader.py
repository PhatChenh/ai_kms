"""
vault/reader.py

Loads a single .md note from disk and returns a hashed, typed Note.

Hash rule: sha256(body.rstrip("\\n").encode("utf-8")).hexdigest()
The rstrip normalises the trailing newline that frontmatter.dumps() always
appends, preventing phantom `modified` events in the indexer when a note's
content has not actually changed.

Callers must not import ``frontmatter`` (the library) directly — use
vault.frontmatter.parse instead.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from core.result import Failure, Result, Success
from vault.frontmatter import NoteMetadata, parse


@dataclass(frozen=True)
class Note:
    """Immutable representation of a parsed vault note."""

    path: Path
    metadata: NoteMetadata
    content: str
    content_hash: str


def read_note(path: Path) -> Result[Note]:
    """
    Load a note from disk and return it with a content hash.

    Args:
        path: Absolute path to the .md file.

    Returns:
        Success(Note) or Failure(recoverable=False) propagated from parse().
    """
    match parse(path):
        case Failure() as f:
            return f
        case Success((metadata, body)):
            normalised = body.rstrip("\n")
            content_hash = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
            return Success(Note(path=path, metadata=metadata, content=normalised, content_hash=content_hash))

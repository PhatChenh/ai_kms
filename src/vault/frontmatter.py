"""
vault/frontmatter.py

Typed wrapper around the ``python-frontmatter`` library.

Callers MUST NOT import ``frontmatter`` (the library) directly — always use
``parse`` and ``dumps`` from this module so all quirk-handling and field
normalisation are centralised in one place.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import frontmatter as _fm
import yaml
from pydantic import BaseModel, Field, field_validator

from core.result import Failure, Result, Success

logger = logging.getLogger(__name__)

# Keys that live explicitly on NoteMetadata; everything else goes in extra.
_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "type",
        "tags",
        "project",
        "created",
        "updated",
        "confidence",
        "updated_by_human",
        "summary",
        "source",
        "source_file",
        "attachment_path",
        "status",
        "source_hash",
        "suggested_project",
        "suggested_primary_domain",
        "classify_confidence",
        "classify_reasoning",
    }
)

# Lazy-migration filter: keys listed here are stripped from dumps() output
# when they appear in metadata.extra, preventing them from being written back
# to disk.  Phase 3A strips "domain" (scalar removed in Phase 3B).
_DEPRECATED_KEYS: frozenset[str] = frozenset({"domain"})


class NoteMetadata(BaseModel):
    """Typed representation of a note's YAML frontmatter."""

    model_config = {"extra": "ignore"}

    type: str | None = None
    tags: list[str] = Field(default_factory=list)
    project: str | None = None
    created: date | None = None
    updated: datetime | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    updated_by_human: bool = False
    summary: str | None = None
    source: str | None = None
    source_file: str | None = None
    attachment_path: str | None = None
    status: str | None = None
    source_hash: str | None = None
    suggested_project: str | None = None
    suggested_primary_domain: str | None = None
    classify_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    classify_reasoning: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "type",
        "project",
        "summary",
        "source",
        "source_file",
        "attachment_path",
        "status",
        "suggested_project",
        "suggested_primary_domain",
        "classify_reasoning",
        mode="before",
    )
    @classmethod
    def _coerce_bool_to_str(cls, v: Any) -> Any:
        """PyYAML 1.1 maps yes/no/on/off/true/false to bool. Coerce back to str."""
        if isinstance(v, bool):
            coerced = "yes" if v else "no"
            logger.debug("frontmatter bool coercion: %s → %r", v, coerced)
            return coerced
        return v


def parse(path: Path) -> Result[tuple[NoteMetadata, str]]:
    """
    Load and parse a markdown note's YAML frontmatter.

    Args:
        path: Absolute path to the .md file.

    Returns:
        Success((NoteMetadata, body_string)) or Failure(recoverable=False).
    """
    try:
        post = _fm.load(str(path))
    except FileNotFoundError as exc:
        return Failure(
            error=f"note not found: {exc}",
            recoverable=False,
            context={"path": str(path)},
        )
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        return Failure(
            error=f"parse error in {path}: {exc}",
            recoverable=False,
            context={"path": str(path)},
        )
    except Exception as exc:
        return Failure(
            error=f"unexpected error reading {path}: {exc}",
            recoverable=False,
            context={"path": str(path)},
        )

    raw: dict[str, Any] = dict(post.metadata)
    known = {k: v for k, v in raw.items() if k in _KNOWN_KEYS}
    unknown = {k: v for k, v in raw.items() if k not in _KNOWN_KEYS}

    try:
        metadata = NoteMetadata(**known, extra=unknown)
    except Exception as exc:
        return Failure(
            error=f"metadata validation error in {path}: {exc}",
            recoverable=False,
            context={"path": str(path)},
        )

    return Success((metadata, post.content))


def dumps(metadata: NoteMetadata, body: str) -> str:
    """
    Serialise metadata + body back to a frontmatter markdown string.

    Args:
        metadata: NoteMetadata instance (None fields are omitted).
        body:     Note body (no frontmatter block).

    Returns:
        Complete string ready to write to disk (frontmatter + body).
    """
    d = metadata.model_dump(exclude_none=True, exclude={"extra"})
    d.update(metadata.extra)

    # Strip deprecated keys that may still appear in extra (lazy migration).
    for key in _DEPRECATED_KEYS:
        d.pop(key, None)

    # Use a custom dumper that writes block-style lists (Obsidian compat).
    class _BlockDumper(yaml.Dumper):
        pass

    def _list_representer(dumper: yaml.Dumper, data: list) -> yaml.Node:
        return dumper.represent_sequence(
            "tag:yaml.org,2002:seq", data, flow_style=False
        )

    _BlockDumper.add_representer(list, _list_representer)

    # python-frontmatter's dumps() uses yaml.dump internally.
    # We need allow_unicode=True (default) and block lists.
    # Rebuild the YAML header ourselves to control dumper.
    if d:
        yaml_str = yaml.dump(
            d,
            Dumper=_BlockDumper,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
        return f"---\n{yaml_str}---\n{body}"
    # No metadata fields — emit bare body.
    return body

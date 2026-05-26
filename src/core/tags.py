"""Tag taxonomy validation and loading for the capture pipeline."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class TagTaxonomy:
    allowed_types: frozenset[str]
    valid_domains: frozenset[str]


# Obsidian tag rules: no spaces; only letters, digits, _, -, /, and accepted Unicode.
# Must have at least one non-digit character per segment (between slashes).
_INVALID_CHARS = re.compile(r"[ \t\r\n#@!.,;:\"'`\\^*+=%&|<>(){}[\]?]")


def _is_valid_obsidian_tag(tag: str) -> bool:
    """Return True if tag satisfies Obsidian's tag format rules.

    Rules:
    - No blank spaces or ASCII punctuation outside [_-/].
    - Each segment (split by /) must contain at least one non-numeric character.
    - Tag must not be empty.
    """
    if not tag:
        return False
    if _INVALID_CHARS.search(tag):
        return False
    # Each segment between slashes must have at least one non-digit char
    for segment in tag.split("/"):
        if not segment:
            return False  # double slash or leading/trailing slash
        if all(c.isdigit() for c in segment):
            return False
    return True


def validate_tags(
    tags: list[str],
    taxonomy: TagTaxonomy,
) -> tuple[list[str], list[str]]:
    """Validate tags against taxonomy and Obsidian format rules; return (valid_tags, violations).

    Args:
        tags: Raw tag list from AI output.
        taxonomy: Loaded taxonomy with allowed types and valid domains.

    Returns:
        Tuple of (valid_tags, violations). Violations are human-readable strings.
    """
    valid: list[str] = []
    violations: list[str] = []
    type_tags_seen = 0

    for tag in tags:
        if not _is_valid_obsidian_tag(tag):
            violations.append(f"invalid Obsidian tag format: {tag!r} — dropped")
            continue

        if tag.startswith("type/"):
            value = tag[len("type/"):]
            if value in taxonomy.allowed_types:
                valid.append(tag)
                type_tags_seen += 1
            else:
                violations.append(f"unknown type tag: {tag!r}")
        elif tag.startswith("domain/"):
            value = tag[len("domain/"):]
            if value in taxonomy.valid_domains:
                valid.append(tag)
            else:
                violations.append(f"unknown domain tag: {tag!r} — not in Domain/ folders")
        elif "/" in tag:
            violations.append(f"free tag has namespace prefix: {tag!r} — stripped")
        else:
            valid.append(tag)

    if type_tags_seen == 0:
        violations.append("no type/ tag found — AI must assign exactly one")
    elif type_tags_seen > 1:
        violations.append(f"multiple type/ tags found ({type_tags_seen}) — only first kept")
        seen_type = False
        deduped: list[str] = []
        for tag in valid:
            if tag.startswith("type/"):
                if not seen_type:
                    deduped.append(tag)
                    seen_type = True
            else:
                deduped.append(tag)
        valid = deduped

    return valid, violations


def load_taxonomy(tags_yaml_path: Path, valid_domains: frozenset[str]) -> TagTaxonomy:
    """Load static vocabulary from tags.yaml; accept pre-scanned domain set.

    Args:
        tags_yaml_path: Path to config/tags.yaml.
        valid_domains: Pre-scanned frozenset of Domain/ folder names.

    Returns:
        TagTaxonomy with allowed_types from file and caller-provided valid_domains.
    """
    raw = yaml.safe_load(tags_yaml_path.read_text())
    return TagTaxonomy(
        allowed_types=frozenset(raw.get("allowed_types", [])),
        valid_domains=valid_domains,
    )

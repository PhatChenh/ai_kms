"""Tag taxonomy validation and loading for the capture pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from core.config import ConfidenceBand, RouteDecision
from core.result import Failure, Result, Success


def normalize_tag_segment(name: str) -> str:
    """Convert a domain/project folder name to a valid Obsidian tag segment.

    Replaces spaces with hyphens so 'AI Competition' → 'AI-Competition'.
    Other characters are left unchanged — callers must ensure they are valid.
    """
    return name.replace(" ", "-")


@dataclass(frozen=True)
class TagTaxonomy:
    allowed_types: frozenset[str]
    valid_domains: frozenset[str]  # normalized slugs (spaces replaced with hyphens)
    domain_folder_names: dict[str, str] = field(
        default_factory=dict
    )  # slug → actual folder name


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
            value = tag[len("type/") :]
            if value in taxonomy.allowed_types:
                valid.append(tag)
                type_tags_seen += 1
            else:
                violations.append(f"unknown type tag: {tag!r}")
        elif tag.startswith("domain/"):
            value = tag[len("domain/") :]
            if value in taxonomy.valid_domains:
                valid.append(tag)
            else:
                violations.append(
                    f"unknown domain tag: {tag!r} — not in Domain/ folders"
                )
        elif "/" in tag:
            violations.append(f"free tag has namespace prefix: {tag!r} — stripped")
        else:
            valid.append(tag)

    if type_tags_seen == 0:
        violations.append("no type/ tag found — AI must assign exactly one")
    elif type_tags_seen > 1:
        violations.append(
            f"multiple type/ tags found ({type_tags_seen}) — only first kept"
        )
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
        valid_domains: Pre-scanned frozenset of Domain/ folder names (actual names,
            may contain spaces). Normalized to valid Obsidian tag segments internally.

    Returns:
        TagTaxonomy with allowed_types from file, normalized valid_domains slugs,
        and domain_folder_names mapping slug → actual folder name.
    """
    raw = yaml.safe_load(tags_yaml_path.read_text())
    slug_map = {normalize_tag_segment(d): d for d in valid_domains}
    return TagTaxonomy(
        allowed_types=frozenset(raw.get("allowed_types", [])),
        valid_domains=frozenset(slug_map.keys()),
        domain_folder_names=slug_map,
    )


# ---------------------------------------------------------------------------
# Dimension rulebook — allowed (dimension, tag) pairs for knowledge extraction
# ---------------------------------------------------------------------------


def validate_dimensions(rulebook: dict) -> Result[dict]:
    """Validate a dimension rulebook has the required nested shape.

    Each dimension must be a dict with:
      - "tags": a list that includes the mandatory "other" catch-all
      - "guidance": a non-empty string

    Args:
        rulebook: Raw dict loaded from dimensions.yaml (or inline fixture).

    Returns:
        Success(rulebook) if valid, Failure describing what's missing otherwise.
    """
    if not isinstance(rulebook, dict):
        return Failure(
            error="dimensions rulebook must be a dict",
            recoverable=False,
            context={"type": type(rulebook).__name__},
        )

    for dim, spec in rulebook.items():
        if not isinstance(spec, dict):
            return Failure(
                error=f"dimension {dim!r} must be a dict with 'tags' and 'guidance'",
                recoverable=False,
                context={"dimension": dim, "type": type(spec).__name__},
            )

        # Validate tags
        tags = spec.get("tags")
        if not isinstance(tags, list):
            return Failure(
                error=f"dimension {dim!r} missing 'tags' key or not a list",
                recoverable=False,
                context={"dimension": dim},
            )
        if "other" not in tags:
            return Failure(
                error=f"dimension {dim!r} missing mandatory 'other' tag in tags list",
                recoverable=False,
                context={"dimension": dim, "tags": tags},
            )

        # Validate guidance
        guidance = spec.get("guidance")
        if not isinstance(guidance, str) or not guidance.strip():
            return Failure(
                error=f"dimension {dim!r} missing 'guidance' or it is empty",
                recoverable=False,
                context={"dimension": dim},
            )

    return Success(value=rulebook)


def load_dimensions(dimensions_yaml_path: Path) -> Result[dict]:
    """Load and validate the dimension rulebook from a dimensions.yaml file.

    Args:
        dimensions_yaml_path: Path to config/dimensions.yaml.

    Returns:
        Success(dict) with nested {dim: {tags, guidance}} shape on valid config.
        Failure if the YAML is malformed or fails validate_dimensions checks.
    """
    try:
        raw = yaml.safe_load(dimensions_yaml_path.read_text())
    except (yaml.YAMLError, OSError) as exc:
        return Failure(
            error=f"failed to load dimensions YAML: {exc}",
            recoverable=False,
            context={"path": str(dimensions_yaml_path)},
        )

    return validate_dimensions(raw)


def validate_dimension_tag(dimension: str, tag: str, rulebook: dict) -> Result[bool]:
    """Check whether a (dimension, tag) pair is allowed by the rulebook.

    Args:
        dimension: The dimension name (e.g. "people", "projects").
        tag: The tag value to check (e.g. "role", "other").
        rulebook: Pre-loaded dict from load_dimensions()
                  (nested shape: {dim: {tags: [...], guidance: "..."}}).

    Returns:
        Success(True) if the pair is allowed.
        Failure describing why the pair is not allowed.
    """
    if dimension not in rulebook:
        return Failure(
            error=f"unknown dimension: {dimension!r}",
            recoverable=False,
            context={"dimension": dimension, "known_dimensions": list(rulebook.keys())},
        )

    allowed_tags = rulebook[dimension]["tags"]
    if tag not in allowed_tags:
        return Failure(
            error=f"unknown tag {tag!r} for dimension {dimension!r}",
            recoverable=False,
            context={"dimension": dimension, "tag": tag, "allowed_tags": allowed_tags},
        )

    return Success(value=True)


def confidence_to_status(score: float, band: ConfidenceBand) -> str:
    """Map a confidence score to a human-readable status using a confidence band.

    Uses band.route() — never compares against threshold floats directly.

    Args:
        score: Confidence score (0.0 to 1.0).
        band: ConfidenceBand with auto/suggest thresholds.

    Returns:
        "confident" for AUTO routing, "pending" for SUGGEST or CLUELESS.
    """
    decision = band.route(score)
    if decision == RouteDecision.AUTO:
        return "confident"
    return "pending"

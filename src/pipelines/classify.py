from __future__ import annotations

import json
from dataclasses import dataclass

from core.config import MainConfig
from core.result import Failure, Success
from core.result import Result
from llm.prompt_loader import PROMPTS
from llm.provider import get_provider


# ---------------------------------------------------------------------------
# Subject Builder — normalize a note into one classify input block
# ---------------------------------------------------------------------------

_MAX_SUBJECT_LENGTH = 3000


def build_subject(
    title: str,
    summary: str | None,
    tags: list[str],
) -> str:
    """Build a single text block from title, summary, and tags for the AI classify prompt.

    Args:
        title: Note title (required — every note has at least a filename).
        summary: Note summary text; None or empty → omitted.
        tags: List of tag strings; empty → omitted.

    Returns:
        Formatted string ready for insertion into the classify prompt template.
        Truncated to _MAX_SUBJECT_LENGTH chars to protect the prompt token budget.
    """
    parts: list[str] = [f"Title: {title}"]

    if summary:
        parts.append(f"Summary: {summary}")

    if tags:
        parts.append(f"Tags: {', '.join(tags)}")

    subject = "\n".join(parts)
    if len(subject) > _MAX_SUBJECT_LENGTH:
        subject = subject[:_MAX_SUBJECT_LENGTH]
    return subject


def build_folder_subject(folder_name: str, file_manifest: str) -> str:
    """Build a subject text block for folder classification.

    Args:
        folder_name: Name of the folder being classified.
        file_manifest: Newline-separated list of filenames in the folder.

    Returns:
        Formatted string ready for insertion into the classify prompt template.
        Truncated to _MAX_SUBJECT_LENGTH chars to protect the prompt token budget.
    """
    parts: list[str] = [f"Folder: {folder_name}", f"Files:\n{file_manifest}"]
    subject = "\n".join(parts)
    if len(subject) > _MAX_SUBJECT_LENGTH:
        subject = subject[:_MAX_SUBJECT_LENGTH]
    return subject


def _destination_names(valid_destinations: str) -> set[str]:
    """Parse the format_for_prompt() block into an exact set of valid names.

    The block has group-header lines like ``Finance:`` and item lines like
    ``  - Alpha``.  Both forms are valid destinations.  The ``Uncategorized``
    group header and the ``No active projects`` placeholder are NOT real
    destinations and are excluded.

    Used for exact-membership validation so a value that is merely a *substring*
    of a real destination (e.g. ``"Alph"`` vs ``"Alpha"``) is rejected.

    # COUPLING: project names and domain names are pooled into one set, so a
    # project name used as a primary_domain (or vice versa) still validates.
    # Closing that cross-type gap needs the structured ProjectRegistry, not the
    # formatted string — tracked separately. This fix only closes the substring
    # hole (the reported defect).
    """
    names: set[str] = set()
    for line in valid_destinations.splitlines():
        token = line.strip()
        if token.startswith("- "):
            token = token[2:].strip()
        elif token.endswith(":"):
            token = token[:-1].strip()
        if token and token not in ("No active projects", "Uncategorized"):
            names.add(token)
    return names


@dataclass(frozen=True)
class ClassifyResult:
    """Result from the classify() pure function.

    Carries the AI's project assignment, domain tags, primary domain,
    confidence, and reasoning.  Validation happens in classify(),
    not here — this dataclass accepts any values.
    """

    project: str | None  # exact project name from destinations, or None
    domains: list[str]  # domain tags applicable to the note
    primary_domain: str | None  # single most relevant domain, or None
    confidence: float  # 0.0 – 1.0
    reasoning: str  # one-sentence explanation from the AI


async def classify(
    subject: str,
    valid_destinations: str,
    config: MainConfig,
) -> Result[ClassifyResult]:
    """Ask the AI which project and domains a note belongs to.

    Pure function — no file writes, no audit log calls, no global config.
    The calling pipeline handles destinations formatting, audit logging,
    confidence routing, and retry.

    Args:
        subject: Pre-built subject text block (use build_subject() to create).
        valid_destinations: Formatted destination list (caller calls
            format_for_prompt() before calling).
        config: Validated MainConfig, passed explicitly for testability.

    Returns:
        Success(ClassifyResult) on valid AI response,
        Failure(recoverable=True) on transient errors,
        Failure(recoverable=False) on code bugs (e.g. template render error).
    """
    # Step 1: Render the prompt template
    try:
        system, user = PROMPTS["classify"].render(
            subject=subject,
            valid_destinations=valid_destinations,
        )
    except Exception as exc:
        return Failure(
            error=f"classify render error: {exc}",
            recoverable=False,
            context={"stage": "classify"},
        )

    # Step 2: Get AI provider
    provider = get_provider("classify", config)

    # Step 3: Call AI
    response = await provider.complete(system, user)

    # Step 4: Handle provider failure
    if isinstance(response, Failure):
        return Failure(
            error=response.error,
            recoverable=True,
            context={"stage": "classify"},
        )

    # Step 5: Parse JSON
    try:
        data = json.loads(response.value.content)
    except json.JSONDecodeError as exc:
        return Failure(
            error=f"classify JSON parse error: {exc}",
            recoverable=True,
            context={
                "stage": "classify",
                "raw": response.value.content[:200],
            },
        )

    # Step 6: Validate required fields (domains, confidence, reasoning)
    required_fields = {"domains", "confidence", "reasoning"}
    missing = required_fields - set(data.keys())
    if missing:
        return Failure(
            error=f"classify missing required fields: {sorted(missing)}",
            recoverable=True,
            context={"stage": "classify", "data_keys": sorted(data.keys())},
        )

    # Step 7: Extract fields
    project = data.get("project")
    domains = data.get("domains")
    primary_domain = data.get("primary_domain")
    confidence = float(data["confidence"])
    reasoning = data["reasoning"]

    # Exact-membership set parsed from the destinations block (not a substring
    # `in` test — "Alph" must not pass for a real destination "Alpha").
    valid_names = _destination_names(valid_destinations)

    # Step 8: Validate project (when set) is an exact valid destination
    if project is not None and project not in valid_names:
        return Failure(
            error=f"classify project {project!r} not in valid destinations",
            recoverable=True,
            context={"stage": "classify", "project": project},
        )

    # Step 9: Validate primary_domain (when set) is an exact valid destination
    if primary_domain is not None and primary_domain not in valid_names:
        return Failure(
            error=f"classify primary_domain {primary_domain!r} not in valid destinations",
            recoverable=True,
            context={"stage": "classify", "primary_domain": primary_domain},
        )

    # Step 10: Validate domains is a list
    if not isinstance(domains, list):
        return Failure(
            error=f"classify domains must be a list, got {type(domains).__name__}",
            recoverable=True,
            context={"stage": "classify", "domains_type": type(domains).__name__},
        )

    # Step 11: Return success
    return Success(
        ClassifyResult(
            project=project,
            domains=domains,
            primary_domain=primary_domain,
            confidence=confidence,
            reasoning=reasoning,
        )
    )

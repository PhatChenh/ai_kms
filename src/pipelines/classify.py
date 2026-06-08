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


@dataclass(frozen=True)
class ClassifyResult:
    """Result from the classify() pure function.

    target_type and target_name together identify the vault folder
    the AI chose for the note.  Validation happens in classify(),
    not here — this dataclass accepts any values.
    """

    target_type: str  # "project" or "domain"
    target_name: str  # exact folder name the AI chose
    confidence: float  # 0.0 – 1.0
    reasoning: str  # one-sentence explanation from the AI


async def classify(
    title: str,
    summary: str,
    tags: str,
    valid_destinations: str,
    config: MainConfig,
) -> Result[ClassifyResult]:
    """Ask the AI which vault folder a note belongs in.

    Pure function — no file writes, no audit log calls, no global config.
    The calling pipeline handles destinations formatting, audit logging,
    confidence routing, and retry.

    Args:
        title: Note title.
        summary: Note summary text.
        tags: Serialised tags string (caller converts list[str] before calling).
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
            title=title,
            summary=summary,
            tags=tags,
            valid_destinations=valid_destinations,
        )
    except Exception as exc:
        return Failure(
            error=f"classify render error: {exc}",
            recoverable=False,
            context={"stage": "classify", "title": title},
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
            context={"stage": "classify", "title": title},
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
                "title": title,
                "raw": response.value.content[:200],
            },
        )

    # Step 6: Validate target_type
    if data.get("target_type") not in {"project", "domain"}:
        return Failure(
            error=f"classify invalid target_type: {data.get('target_type')!r}",
            recoverable=True,
            context={"target_type": data.get("target_type"), "title": title},
        )

    # Step 7: Return success
    return Success(
        ClassifyResult(
            target_type=data["target_type"],
            target_name=data["target_name"],
            confidence=float(data["confidence"]),
            reasoning=data["reasoning"],
        )
    )

"""
pipelines/classify_extract.py

Entity extraction and validation for the classify pipeline.

Extracted from pipelines/classify.py -- move-only refactoring, no logic changes.
"""

from __future__ import annotations

import json

from core.config import MainConfig
from core.result import Failure, Result, Success
from llm.prompt_loader import PROMPTS
from llm.provider import get_provider


def _validate_item(
    item: dict, action: str, index: int, dimension: str, raw_text: str
) -> Failure | dict:
    def _fail(msg: str) -> Failure:
        return Failure(
            error=f"entity_extract item[{index}] {msg}",
            recoverable=True,
            context={"stage": "extract", "dimension": dimension, "raw": raw_text[:200]},
        )

    REQUIRED = {
        "retire": {"id", "reason"},
        "update": {"id", "entity", "tag", "fact", "confidence"},
        "new": {"entity", "tag", "fact", "confidence"},
    }
    FORBIDDEN = {"new": {"id"}}

    required = REQUIRED.get(action)
    if required is None:
        return _fail(f"unknown action {action!r}")

    for field_name in required:
        if field_name not in item:
            return _fail(f"'{action}' missing '{field_name}'")

    for field_name in FORBIDDEN.get(action, set()):
        if field_name in item:
            return _fail(f"'{action}' must not include '{field_name}'")

    if action == "new":
        for field_name in ("entity", "tag", "fact"):
            if not str(item[field_name]).strip():
                return _fail(f"'new' {field_name!r} must not be empty")
        conf_val = float(item["confidence"])
        if conf_val < 0.0 or conf_val > 1.0:
            return _fail(f"confidence {conf_val} out of range [0.0, 1.0]")

    if action == "retire":
        return {
            "action": "retire",
            "id": item["id"],
            "reason": item.get("reason", ""),
        }
    elif action == "update":
        return {
            "action": "update",
            "id": item["id"],
            "entity": item["entity"],
            "tag": item["tag"],
            "fact": item["fact"],
            "confidence": float(item["confidence"]),
        }
    else:
        return {
            "action": "new",
            "entity": item["entity"],
            "tag": item["tag"],
            "fact": item["fact"],
            "confidence": float(item["confidence"]),
        }


async def extract(
    dimension: str,
    text: str,
    existing_facts: list,
    guidance: str,
    feedback: str,
    config: MainConfig,
) -> Result[list[dict]]:
    """Ask the AI to extract structured facts from *text* for *dimension*.

    Returns a list of parsed fact dicts, each validated against the
    entity_extract prompt's reply contract.  The caller is responsible for
    routing each fact (new / update / retire).

    Args:
        dimension:     The knowledge category name (e.g. "people").
        text:          The document text from Content Reader.
        existing_facts: List of KnowledgeEntry-like objects with .id, .entity,
                        .tag, .fact, .confidence attributes.
        guidance:      The dimension's guidance text from dimensions.yaml.
        feedback:      The previous failure reason (empty string on first attempt).
        config:        Validated MainConfig.

    Returns:
        Success(list[dict]) with parsed facts, or Failure with a recoverable
        flag set per the error class.
    """
    # 1. Render the prompt
    try:
        system, user = PROMPTS["entity_extract"].render(
            document_text=text,
            dimension_guidance=guidance,
            existing_facts=existing_facts,
            previous_attempt_feedback=feedback,
        )
    except Exception as exc:
        return Failure(
            error=f"entity_extract render error: {exc}",
            recoverable=False,
            context={"stage": "extract", "dimension": dimension},
        )

    # 2. Get the AI provider via the factory (never instantiate directly)
    provider = get_provider("classify", config)

    # 3. Call the AI
    response = await provider.complete(system, user)

    # 4. Handle provider failure
    if isinstance(response, Failure):
        return Failure(
            error=response.error,
            recoverable=True,
            context={"stage": "extract", "dimension": dimension},
        )

    # 5. Parse JSON
    raw_text = response.value.content
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return Failure(
            error=f"entity_extract JSON parse error: {exc}",
            recoverable=True,
            context={
                "stage": "extract",
                "dimension": dimension,
                "raw": raw_text[:200],
            },
        )

    # 6. Validate top-level is a list
    if not isinstance(data, list):
        return Failure(
            error=f"entity_extract reply must be a JSON array, got {type(data).__name__}",
            recoverable=True,
            context={
                "stage": "extract",
                "dimension": dimension,
                "raw": raw_text[:200],
            },
        )

    # 7. Validate each fact
    parsed: list[dict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            return Failure(
                error=f"entity_extract item[{i}] is not a dict: {type(item).__name__}",
                recoverable=True,
                context={
                    "stage": "extract",
                    "dimension": dimension,
                    "raw": raw_text[:200],
                },
            )

        action = item.get("action")
        result = _validate_item(item, action, i, dimension, raw_text)
        if isinstance(result, Failure):
            return result
        parsed.append(result)

    return Success(parsed)

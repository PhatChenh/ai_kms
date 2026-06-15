from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

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

    This is the backward-compat path — callers with a ProjectRegistry should
    use the ``project_names`` / ``domain_names`` params on classify() instead
    to get cross-type validation (TD-051).
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
    project_names: frozenset[str] | None = None,
    domain_names: frozenset[str] | None = None,
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

    # Exact-membership validation.
    # When both project_names and domain_names are provided, validate against
    # the typed sets (cross-type — TD-051).  Otherwise fall back to the pooled
    # set parsed from valid_destinations (backward compat for callers that
    # don't have a ProjectRegistry).
    if project_names is not None and domain_names is not None:
        # Step 8: Validate project (when set) is an exact project name
        if project is not None and project not in project_names:
            return Failure(
                error=f"classify project {project!r} not in project_names (cross-type: is it a domain?)",
                recoverable=True,
                context={"stage": "classify", "project": project},
            )

        # Step 9: Validate primary_domain (when set) is an exact domain name
        if primary_domain is not None and primary_domain not in domain_names:
            return Failure(
                error=f"classify primary_domain {primary_domain!r} not in domain_names (cross-type: is it a project?)",
                recoverable=True,
                context={"stage": "classify", "primary_domain": primary_domain},
            )
    else:
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


# ===========================================================================
# Phase 6 — Content Reader + Context Loader (classify infra helpers)
# ===========================================================================


def content_reader(
    doc_id: int,
    *,
    config: MainConfig,
    db_path: Path | None = None,
) -> Result[str]:
    """Choose full_body or summary for a document based on token budget.

    Uses the // 4 heuristic to estimate token count from character length.
    If full_body fits within config.classify.max_content_tokens, it is used;
    otherwise the summary is used instead.  When full_body is None or empty,
    summary is always used as a fallback.

    Args:
        doc_id: Primary key of the documents row.
        config: Validated MainConfig carrying the classify token cap.
        db_path: Override DB path.

    Returns:
        Success(str) with the chosen text, or Failure if the row is missing
        or the DB is unreachable.
    """
    from storage.db import get_connection

    try:
        with get_connection(db_path, readonly=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT full_body, summary FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        return Failure(
            error=str(exc),
            recoverable=False,
            context={"doc_id": doc_id, "op": "content_reader"},
        )

    if row is None:
        return Failure(
            error=f"document not found: id={doc_id}",
            recoverable=False,
            context={"doc_id": doc_id, "op": "content_reader"},
        )

    full_body: str | None = row["full_body"]
    summary: str | None = row["summary"]

    # Fallback: None or empty full_body → summary
    if not full_body:
        return Success(summary or "")

    max_tokens: int = config.classify.max_content_tokens
    estimated_tokens = len(full_body) // 4

    if estimated_tokens < max_tokens:
        return Success(full_body)

    return Success(summary or "")


def context_loader(
    *,
    config: MainConfig,
    db_path: Path | None = None,
) -> Result[dict[str, list]]:
    """Load ranked, capped, non-retired knowledge entries for every dimension.

    Reads the dimension list from config/dimensions.yaml (Phase 2), then for
    each dimension calls the Phase 4 ranked query with
    ``config.classify.max_entries_per_dimension`` as the cap.

    Args:
        config:  Validated MainConfig carrying the per-dimension cap.
        db_path: Override DB path.

    Returns:
        Success(dict[dimension → list[KnowledgeEntry]]) with ranked, capped,
        non-retired facts.  Dimensions with zero matching facts get an empty
        list (not an error).
    """
    from core.tags import load_dimensions
    from storage.knowledge_entries import query_ranked_by_dimension

    # Locate dimensions.yaml relative to this source file
    _classify_dir = Path(__file__).resolve().parent  # src/pipelines
    _project_root = _classify_dir.parent.parent  # project root
    dimensions_path = _project_root / "config" / "dimensions.yaml"

    dims_result = load_dimensions(dimensions_path)
    if isinstance(dims_result, Failure):
        return Failure(
            error=f"context_loader cannot load dimensions: {dims_result.error}",
            recoverable=False,
            context={"op": "context_loader", "path": str(dimensions_path)},
        )

    rulebook: dict = dims_result.value
    cap: int = config.classify.max_entries_per_dimension

    result: dict[str, list] = {}

    for dim_name in rulebook:
        ranked = query_ranked_by_dimension(
            dim_name,
            limit=cap,
            db_path=db_path,
        )
        if isinstance(ranked, Failure):
            return Failure(
                error=f"context_loader query failed for dimension {dim_name!r}: {ranked.error}",
                recoverable=False,
                context={"op": "context_loader", "dimension": dim_name},
            )
        result[dim_name] = ranked.value

    return Success(result)


# ===========================================================================
# Phase 7 — Work Queue + Worker + catch-up scan
# ===========================================================================

import asyncio
import logging

_worker_log = logging.getLogger(__name__)


async def consumer(
    queue: asyncio.Queue[int],
    db_path: Path | None,
    config: MainConfig,
) -> None:
    """Single sequential consumer: pulls doc_ids from the queue, prepares
    inputs via Content Reader → Dimension Loader → Context Loader, then
    **stops** — this is the Slice B seam.  No AI call, no stamp on the
    happy path.

    Failures at any stage are logged and the doc is left un-stamped so it
    will be retried on the next startup catch-up scan.
    """
    while True:
        got_item = False
        try:
            doc_id = await queue.get()
            got_item = True

            # ---- Content Reader ----
            cr = content_reader(doc_id, config=config, db_path=db_path)
            if isinstance(cr, Failure):
                _worker_log.warning(
                    "classify_worker content_reader failed doc_id=%s error=%s",
                    doc_id,
                    cr.error,
                )
                continue

            # ---- Dimension Loader (no-op: dimensions are loaded by context_loader) ----
            # The design says "Dimension Loader → Context Loader".
            # load_dimensions is called internally by context_loader.

            # ---- Context Loader ----
            cl = context_loader(config=config, db_path=db_path)
            if isinstance(cl, Failure):
                _worker_log.warning(
                    "classify_worker context_loader failed doc_id=%s error=%s",
                    doc_id,
                    cl.error,
                )
                continue

            # ---- Slice B seam ----
            # Slice B will insert the AI classify() call here and, on
            # success, call stamp_classified(doc_id, db_path=db_path).
            # Until then we intentionally leave classify_content_hash
            # untouched so every doc is retried on restart.

        except Exception:
            _worker_log.exception(
                "classify_worker unexpected error doc_id=%s", doc_id
            )
        finally:
            if got_item:
                queue.task_done()


async def catch_up_scan(
    queue: asyncio.Queue[int],
    db_path: Path | None,
) -> None:
    """One burst at startup: discover every doc that needs classification
    and enqueue its id.

    OQ-P8A-03 — catch-up scan.
    """
    from storage.documents import find_unclassified

    result = find_unclassified(db_path=db_path)
    if isinstance(result, Failure):
        _worker_log.error(
            "catch_up_scan find_unclassified failed error=%s",
            result.error,
        )
        return

    for doc_id in result.value:
        queue.put_nowait(doc_id)

    _worker_log.info("catch_up_scan enqueued=%d", len(result.value))


# ===========================================================================
# Phase 5 — Entity Extractor (Slice B)
# ===========================================================================


@dataclass
class _ExtractFact:
    """One parsed fact from the AI reply — internal to extract()."""
    action: str
    entity: str
    tag: str
    fact: str = ""
    confidence: float = 0.5
    id: int | None = None
    reason: str = ""


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
        if action not in ("new", "update", "retire"):
            return Failure(
                error=f"entity_extract item[{i}] unknown action {action!r}",
                recoverable=True,
                context={
                    "stage": "extract",
                    "dimension": dimension,
                    "raw": raw_text[:200],
                },
            )

        # Per-action field validation
        if action == "retire":
            # retire: requires id + reason
            if "id" not in item:
                return Failure(
                    error=f"entity_extract item[{i}] 'retire' missing 'id'",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            if "reason" not in item:
                return Failure(
                    error=f"entity_extract item[{i}] 'retire' missing 'reason'",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            parsed.append({
                "action": "retire",
                "id": item["id"],
                "reason": item.get("reason", ""),
            })

        elif action == "update":
            # update: requires id, entity, tag, fact, confidence
            if "id" not in item:
                return Failure(
                    error=f"entity_extract item[{i}] 'update' missing 'id'",
                    recoverable=True,
                    context={
                        "stage": "extract",
                        "dimension": dimension,
                        "raw": raw_text[:200],
                    },
                )
            for field in ("entity", "tag", "fact", "confidence"):
                if field not in item:
                    return Failure(
                        error=f"entity_extract item[{i}] 'update' missing {field!r}",
                        recoverable=True,
                        context={
                            "stage": "extract",
                            "dimension": dimension,
                            "raw": raw_text[:200],
                        },
                    )
            parsed.append({
                "action": "update",
                "id": item["id"],
                "entity": item["entity"],
                "tag": item["tag"],
                "fact": item["fact"],
                "confidence": float(item["confidence"]),
            })

        elif action == "new":
            # new: requires entity, tag, fact, confidence; must NOT have id
            for field in ("entity", "tag", "fact", "confidence"):
                if field not in item:
                    return Failure(
                        error=f"entity_extract item[{i}] 'new' missing {field!r}",
                        recoverable=True,
                        context={
                            "stage": "extract",
                            "dimension": dimension,
                            "raw": raw_text[:200],
                        },
                    )
            parsed.append({
                "action": "new",
                "entity": item["entity"],
                "tag": item["tag"],
                "fact": item["fact"],
                "confidence": float(item["confidence"]),
            })

    return Success(parsed)

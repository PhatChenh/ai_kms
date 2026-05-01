"""
test_logging.py
───────────────
Verifies two things:
    1. Logging setup works correctly (JSON output, correlation_id isolation)
    2. Result types (Success / Failure) are compatible with the logging setup

Prerequisites:
    - core/logging_setup.py is in place
    - core/result.py has the to_log_dict() method on Failure

Run with:
    python test_logging.py
    cat logs/kms.log   ← inspect the raw JSON output
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import structlog

from core.logging_setup import setup_logging, new_correlation_id
from core.result import Failure, Success

# ─── Test runner helpers ──────────────────────────────────────────────────────
# A minimal manual test runner — no pytest dependency needed.
# Each test_ function returns (passed: bool, message: str).

_results: list[tuple[str, bool, str]] = []


def run_test(name: str, fn) -> None:
    """Execute fn(), record pass/fail, print result."""
    try:
        passed, message = fn()
    except Exception as e:
        passed, message = False, f"raised unexpected exception: {type(e).__name__}: {e}"

    _results.append((name, passed, message))
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {name}")
    if not passed:
        print(f"         → {message}")


def print_summary() -> None:
    total = len(_results)
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n{'─' * 60}")
    print(f"  {passed}/{total} tests passed")
    if passed < total:
        print("  Failed:")
        for name, ok, msg in _results:
            if not ok:
                print(f"    • {name}: {msg}")
    print(f"{'─' * 60}")


def read_log_lines() -> list[dict]:
    """Read kms.log and return all valid JSON entries as dicts."""
    log_file = Path("logs/kms.log")
    if not log_file.exists():
        return []
    lines = []
    for raw in log_file.read_text().strip().splitlines():
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            pass
    return lines


def lines_for_run(correlation_id: str) -> list[dict]:
    """Filter log lines belonging to a specific pipeline run."""
    return [e for e in read_log_lines() if e.get("correlation_id") == correlation_id]


# ─── Section 1: Core logging setup ───────────────────────────────────────────

def test_log_file_created() -> tuple[bool, str]:
    """logs/kms.log must exist after setup_logging() is called."""
    exists = Path("logs/kms.log").exists()
    return exists, "logs/kms.log not found"


def test_all_lines_are_valid_json() -> tuple[bool, str]:
    """Every line in kms.log must parse as JSON."""
    log_file = Path("logs/kms.log")
    if not log_file.exists():
        return False, "log file missing"
    for raw in log_file.read_text().strip().splitlines():
        try:
            json.loads(raw)
        except json.JSONDecodeError:
            return False, f"non-JSON line: {raw[:80]}"
    return True, ""


def test_required_fields_present(correlation_id: str) -> tuple[bool, str]:
    """Every log line must have timestamp, level, logger, event, correlation_id."""
    required = {"timestamp", "level", "logger", "event", "correlation_id"}
    for entry in lines_for_run(correlation_id):
        missing = required - entry.keys()
        if missing:
            return False, f"missing fields {missing} in: {entry.get('event')}"
    return True, ""


def test_correlation_id_isolation(run_a_id: str, run_b_id: str) -> tuple[bool, str]:
    """
    Lines from run A must only carry run A's correlation_id, and vice versa.
    This proves clear_contextvars() works between runs.
    """
    if run_a_id == run_b_id:
        return False, "both runs got the same correlation_id"

    a_lines = lines_for_run(run_a_id)
    b_lines = lines_for_run(run_b_id)

    if len(a_lines) == 0:
        return False, "no log lines found for run A"
    if len(b_lines) == 0:
        return False, "no log lines found for run B"

    # Check none of run B's lines accidentally carry run A's id
    for entry in b_lines:
        if entry.get("correlation_id") == run_a_id:
            return False, "run B line carries run A's correlation_id — context bleed"

    return True, ""


# ─── Section 2: Result type compatibility ────────────────────────────────────

def test_failure_to_log_dict_is_json_serializable() -> tuple[bool, str]:
    """
    Failure.to_log_dict() must return a dict that json.dumps() accepts.
    This is the core contract — if this breaks, every pipeline's error
    logging silently drops the log line.
    """
    fail = Failure(
        error="LLM timeout after 60s",
        recoverable=True,
        context={"note_id": "note_001", "stage": "classify"},
    )
    try:
        serialized = json.dumps(fail.to_log_dict())
    except TypeError as e:
        return False, f"to_log_dict() produced non-serializable value: {e}"

    # Also verify the keys we expect are actually there
    parsed = json.loads(serialized)
    expected_keys = {"error", "recoverable", "context", "traceback"}
    missing = expected_keys - parsed.keys()
    if missing:
        return False, f"to_log_dict() is missing keys: {missing}"

    return True, ""


def test_failure_fields_appear_in_log(correlation_id: str) -> tuple[bool, str]:
    """
    When a pipeline logs a Failure via **f.to_log_dict(), the error,
    recoverable, and context fields must appear as top-level keys in
    the JSON log line — not nested under a 'failure' key.
    """
    lines = lines_for_run(correlation_id)
    failure_lines = [e for e in lines if e.get("event") == "stage failed"]

    if not failure_lines:
        return False, "no 'stage failed' log line found for this run"

    entry = failure_lines[0]

    checks = {
        "error": lambda v: isinstance(v, str) and len(v) > 0,
        "recoverable": lambda v: isinstance(v, bool),
        "context": lambda v: isinstance(v, dict),
    }

    for field, validator in checks.items():
        if field not in entry:
            return False, f"field '{field}' not found in log entry"
        if not validator(entry[field]):
            return False, f"field '{field}' has unexpected value: {entry[field]}"

    return True, ""


def test_failure_with_nonserializable_context() -> tuple[bool, str]:
    """
    Failure.to_log_dict() must handle non-serializable context values
    (Path, datetime) by coercing them to strings. Without this, the
    log entry silently disappears whenever a handler passes a Path object.
    """
    fail = Failure(
        error="vault write failed",
        recoverable=False,
        context={
            "path": Path("inbox/meeting_notes.md"),      # Path is not JSON-serializable
            "attempted_at": datetime(2026, 5, 1, 14, 0, 0),  # datetime is not either
        },
    )
    try:
        serialized = json.dumps(fail.to_log_dict())
    except TypeError as e:
        return False, (
            f"to_log_dict() did not coerce non-serializable context values: {e}\n"
            f"         Fix: use {{k: str(v) for k, v in self.context.items()}} in to_log_dict()"
        )

    parsed = json.loads(serialized)
    context = parsed.get("context", {})

    if not isinstance(context.get("path"), str):
        return False, "Path in context was not coerced to string"
    if not isinstance(context.get("attempted_at"), str):
        return False, "datetime in context was not coerced to string"

    return True, ""


def test_failure_auto_captures_traceback() -> tuple[bool, str]:
    """
    When Failure is created inside an except block, __post_init__ must
    auto-capture the traceback string. This is what makes error logs
    debuggable without manually passing exc_info everywhere.
    """
    try:
        raise ValueError("simulated LLM parse error")
    except ValueError:
        fail = Failure(
            error="LLM returned unparseable JSON",
            recoverable=True,
            context={"stage": "summarize"},
        )

    if fail.traceback is None:
        return False, "traceback was not auto-captured inside except block"
    if "ValueError" not in fail.traceback:
        return False, f"traceback does not mention the exception type: {fail.traceback[:100]}"

    return True, ""


def test_failure_traceback_is_none_outside_except() -> tuple[bool, str]:
    """
    When Failure is created outside an except block (e.g. a validation
    failure, not an exception), traceback must be None — not a misleading
    'NoneType: None' string.
    """
    fail = Failure(
        error="confidence score below threshold",
        recoverable=False,
        context={"score": 0.45, "threshold": 0.60},
    )
    if fail.traceback is not None:
        return False, (
            f"traceback should be None outside an except block, got: {fail.traceback[:80]}"
        )
    return True, ""


def test_success_value_is_loggable() -> tuple[bool, str]:
    """
    Success.value should be loggable directly when it's a primitive.
    This is the happy path — no special handling needed, just documenting
    that it works as expected.
    """
    result = Success(value={"note_id": "note_001", "classification": "research"})
    try:
        json.dumps({"value": result.value})
    except TypeError as e:
        return False, f"Success.value is not JSON-serializable: {e}"
    return True, ""


def test_direct_failure_object_is_not_serializable() -> tuple[bool, str]:
    """
    DOCUMENTS THE BAD PATTERN: passing the Failure object itself as a log
    field (logger.error('msg', failure=f)) will fail JSONRenderer.

    This test verifies the failure mode exists so developers understand WHY
    to_log_dict() is required. It passes if json.dumps raises TypeError.
    """
    fail = Failure(
        error="some error",
        recoverable=True,
        context={},
    )
    try:
        json.dumps({"failure": fail})
        return False, "Failure object serialized directly — to_log_dict() may be unnecessary"
    except TypeError:
        # Expected — dataclass is NOT JSON-serializable, confirming the bad
        # pattern would silently drop log entries in production.
        return True, ""


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Startup ──────────────────────────────────────────────────────────────
    setup_logging(log_level="DEBUG", dev_mode=True)
    logger = structlog.get_logger(__name__)

    # ── Produce log lines for the core logging tests ──────────────────────────
    run_a_id = new_correlation_id()
    logger.info("pipeline started", pipeline="capture", source="inbox/meeting_notes.md")
    logger.debug("handler selected", handler="MarkdownHandler")
    logger.info("extraction complete", word_count=342, note_id="note_001")
    logger.warning("low confidence score", score=0.58, threshold=0.60)

    run_b_id = new_correlation_id()
    logger.info("pipeline started", pipeline="capture", source="inbox/q2_report.pdf")
    logger.info("extraction complete", word_count=1204, note_id="note_002")

    # ── Produce log lines for the Result compatibility tests ──────────────────
    # Using a dedicated run so we can isolate its log lines cleanly.
    result_run_id = new_correlation_id()
    fail = Failure(
        error="LLM timeout after 60s",
        recoverable=True,
        context={"note_id": "note_001", "stage": "classify"},
    )
    logger.error("stage failed", **fail.to_log_dict())

    # ── Run tests ─────────────────────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("  SECTION 1: Core logging setup")
    print("─" * 60)
    run_test("log file created",
             test_log_file_created)
    run_test("all lines are valid JSON",
             test_all_lines_are_valid_json)
    run_test("required fields present on every line",
             lambda: test_required_fields_present(run_a_id))
    run_test("correlation_id isolated between runs",
             lambda: test_correlation_id_isolation(run_a_id, run_b_id))

    print("\n" + "─" * 60)
    print("  SECTION 2: Result type compatibility")
    print("─" * 60)
    run_test("Failure.to_log_dict() is JSON-serializable",
             test_failure_to_log_dict_is_json_serializable)
    run_test("Failure fields appear as top-level keys in log",
             lambda: test_failure_fields_appear_in_log(result_run_id))
    run_test("Failure context coerces non-serializable values (Path, datetime)",
             test_failure_with_nonserializable_context)
    run_test("Failure auto-captures traceback inside except block",
             test_failure_auto_captures_traceback)
    run_test("Failure traceback is None outside except block",
             test_failure_traceback_is_none_outside_except)
    run_test("Success.value is loggable when it is a primitive",
             test_success_value_is_loggable)
    run_test("direct Failure object is NOT serializable (documents bad pattern)",
             test_direct_failure_object_is_not_serializable)

    print_summary()

    # Non-zero exit code if any test failed — useful for CI later
    if any(not ok for _, ok, _ in _results):
        sys.exit(1)


if __name__ == "__main__":
    main()
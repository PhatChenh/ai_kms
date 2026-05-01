"""
core/result.py

Pattern: Result Type
Every pipeline stage returns Success[T] or Failure — never raises silently.
Callers must handle both branches.

Branching convention: prefer `match/case` in pipeline stages,
`isinstance` is acceptable in tests and scripts.
"""

import traceback as tb
from dataclasses import dataclass, field
from typing import Never
from exceptions import KMSError

# ---------------------------------------------------------------------------
# Core result types
# ---------------------------------------------------------------------------

@dataclass
class Success[T]:
    """Wraps a successful pipeline output."""
    value: T

    def is_success(self) -> bool:
        return True

    def is_failure(self) -> bool:
        return False

    def unwrap(self) -> T:
        """Return the value. Safe to call — this is a Success."""
        return self.value


@dataclass
class Failure:
    """
    Wraps a failed pipeline stage.

    Fields:
        error       — human-readable description of what went wrong
        recoverable — True means caller may retry; False means discard
        context     — arbitrary dict with debugging info (note_id, stage, etc.)
        traceback   — auto-captured from the active exception, if any
    """
    error: str
    recoverable: bool
    context: dict
    traceback: str | None = field(default=None)

    def __post_init__(self) -> None:
        # Auto-capture current exception traceback if one is active
        # and the caller didn't supply one explicitly.
        if self.traceback is None:
            captured = tb.format_exc()
            self.traceback = (
                None if captured.strip() == "NoneType: None" else captured
            )

    def is_success(self) -> bool:
        return False

    def is_failure(self) -> bool:
        return True

    def unwrap(self) -> Never:
        """Always raises. Forces callers to handle Failure explicitly."""
        raise KMSError(self.error)


# ---------------------------------------------------------------------------
# Type alias — use this in all function signatures
# ---------------------------------------------------------------------------

type Result[T] = Success[T] | Failure


# ---------------------------------------------------------------------------
# Usage example (for documentation purposes — not executed at import)
# ---------------------------------------------------------------------------
#
# def classify_note(note: str) -> Result[ClassifiedNote]:
#     try:
#         result = ai_classify(note)
#         return Success(value=result)
#     except TimeoutError as e:
#         return Failure(
#             error=str(e),
#             recoverable=True,
#             context={"note_preview": note[:100], "stage": "classify"},
#         )
#
# Caller using match/case:
#     match classify_note(raw):
#         case Success(value=classified):
#             store(classified)
#         case Failure(recoverable=True) as f:
#             retry_queue.add(raw)
#         case Failure() as f:
#             audit.log(f.error)
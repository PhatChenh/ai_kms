from __future__ import annotations

import json
from dataclasses import dataclass


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

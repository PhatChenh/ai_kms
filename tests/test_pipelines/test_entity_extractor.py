"""Tests for entity extractor — extract() function in pipelines/classify_extract.py."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.config import ClassifyConfig, MainConfig
from core.result import Failure, Success


# ---------------------------------------------------------------------------
# Fake provider for testing
# ---------------------------------------------------------------------------


@dataclass
class _FakeCompleteResult:
    """Mimics the return value of provider.complete()."""

    content: str


class _FakeProvider:
    """A stub provider whose complete() returns whatever string we set."""

    def __init__(self, reply: str | None = None):
        self._reply = reply
        self._call_count = 0
        self._last_system: str | None = None
        self._last_user: str | None = None

    async def complete(self, system: str, user: str):
        self._call_count += 1
        self._last_system = system
        self._last_user = user
        if self._reply is None:
            return Failure("simulated provider failure", recoverable=True, context={})
        return Success(_FakeCompleteResult(content=self._reply))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(vault_dir: str) -> MainConfig:
    """Build a minimal MainConfig for extract() tests."""
    from core.config import VaultConfig

    return MainConfig(
        vault=VaultConfig(root=vault_dir),
        classify=ClassifyConfig(max_retries=3),
    )


def _valid_reply() -> str:
    """Return a valid JSON reply per the entity_extract schema."""
    return json.dumps(
        [
            {
                "action": "new",
                "entity": "Anthony",
                "tag": "other",
                "fact": "Anthony leads the Movie Q2 project.",
                "confidence": 0.9,
            },
        ]
    )


def _valid_reply_multiple() -> str:
    """Return a valid reply with multiple actions."""
    return json.dumps(
        [
            {
                "action": "new",
                "entity": "Anthony",
                "tag": "other",
                "fact": "Anthony leads Movie Q2.",
                "confidence": 0.9,
            },
            {
                "action": "update",
                "id": 1,
                "entity": "Anthony",
                "tag": "other",
                "fact": "Anthony works in the Movies division.",
                "confidence": 0.85,
            },
            {
                "action": "retire",
                "id": 2,
                "reason": "No longer relevant — project completed.",
            },
        ]
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntityExtractor:
    """Phase 5 Slice B — extract() function."""

    async def test_valid_reply_returns_parsed_facts(self, tmp_path, monkeypatch):
        """A valid JSON reply is parsed into a list of fact dicts."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply=_valid_reply())
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Anthony leads the Movie Q2 project.",
            existing_facts=[],
            guidance="Tag: other — extract general facts.",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Success), f"Expected Success, got {result}"
        facts = result.value
        assert len(facts) == 1
        f = facts[0]
        assert f["action"] == "new"
        assert f["entity"] == "Anthony"
        assert f["tag"] == "other"
        assert f["confidence"] == 0.9
        assert "id" not in f  # new facts omit id

    async def test_reply_with_multiple_actions_returns_all(self, tmp_path, monkeypatch):
        """Multiple actions (new, update, retire) are all returned."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply=_valid_reply_multiple())
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Success)
        facts = result.value
        assert len(facts) == 3
        actions = {f["action"] for f in facts}
        assert actions == {"new", "update", "retire"}

    async def test_update_action_carries_id(self, tmp_path, monkeypatch):
        """Update actions must carry the referenced id."""
        from pipelines.classify_extract import extract

        reply = json.dumps(
            [
                {
                    "action": "update",
                    "id": 42,
                    "entity": "Anthony",
                    "tag": "other",
                    "fact": "Updated fact.",
                    "confidence": 0.8,
                }
            ]
        )
        provider = _FakeProvider(reply=reply)
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Success)
        fact = result.value[0]
        assert fact["id"] == 42
        assert fact["action"] == "update"

    async def test_retire_action_requires_reason(self, tmp_path, monkeypatch):
        """Retire actions must carry a reason field."""
        from pipelines.classify_extract import extract

        reply = json.dumps(
            [
                {
                    "action": "retire",
                    "id": 5,
                    "reason": "Fact is outdated.",
                }
            ]
        )
        provider = _FakeProvider(reply=reply)
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Success)
        fact = result.value[0]
        assert fact["action"] == "retire"
        assert fact["id"] == 5
        assert fact["reason"] == "Fact is outdated."

    async def test_unparseable_reply_returns_recoverable_failure(
        self, tmp_path, monkeypatch
    ):
        """An unparseable (non-JSON) reply → recoverable Failure."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply="not valid json at all")
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True
        # Context should carry a truncated snippet of the raw reply
        raw = result.context.get("raw", "") if result.context else ""
        assert len(raw) <= 200

    async def test_missing_required_fields_returns_recoverable_failure(
        self, tmp_path, monkeypatch
    ):
        """A new fact missing 'entity' → recoverable Failure."""
        from pipelines.classify_extract import extract

        reply = json.dumps(
            [{"action": "new", "tag": "other", "fact": "x", "confidence": 0.5}]
        )
        provider = _FakeProvider(reply=reply)
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True

    async def test_update_missing_id_returns_recoverable_failure(
        self, tmp_path, monkeypatch
    ):
        """An 'update' action without an id → recoverable Failure."""
        from pipelines.classify_extract import extract

        reply = json.dumps(
            [
                {
                    "action": "update",
                    "entity": "Anthony",
                    "tag": "other",
                    "fact": "x",
                    "confidence": 0.5,
                }
            ]
        )
        provider = _FakeProvider(reply=reply)
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True

    async def test_provider_failure_returns_recoverable_failure(
        self, tmp_path, monkeypatch
    ):
        """A provider that returns Failure → recoverable Failure."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply=None)  # None → Failure
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True

    async def test_uses_provider_factory(self, tmp_path, monkeypatch):
        """The extractor calls get_provider('classify', config) — never
        instantiates a provider directly."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply=_valid_reply())

        captured_task = None

        def fake_get_provider(task, cfg):
            nonlocal captured_task
            captured_task = task
            return provider

        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", fake_get_provider
        )

        await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert captured_task == "classify"
        assert provider._call_count == 1

    async def test_feedback_is_passed_to_prompt(self, tmp_path, monkeypatch):
        """The feedback string is rendered into the prompt."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply=_valid_reply())
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="Last attempt failed: bad JSON",
            config=_make_config(str(tmp_path)),
        )

        # The user template should contain the feedback text
        assert provider._last_user is not None
        assert "Last attempt failed: bad JSON" in provider._last_user

    async def test_empty_array_reply_is_valid(self, tmp_path, monkeypatch):
        """An empty JSON array is a valid response (no facts for this dimension)."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply="[]")
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Success)
        assert result.value == []

    async def test_reply_not_a_list_returns_recoverable_failure(
        self, tmp_path, monkeypatch
    ):
        """A JSON object (not an array) → recoverable Failure."""
        from pipelines.classify_extract import extract

        provider = _FakeProvider(reply='{"action": "new"}')
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True

    async def test_new_fact_with_spurious_id_is_rejected(self, tmp_path, monkeypatch):
        """I6 — A 'new' action with an 'id' field → recoverable Failure."""
        from pipelines.classify_extract import extract

        reply = json.dumps(
            [
                {
                    "action": "new",
                    "id": 99,
                    "entity": "Anthony",
                    "tag": "other",
                    "fact": "Test.",
                    "confidence": 0.9,
                }
            ]
        )
        provider = _FakeProvider(reply=reply)
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True

    async def test_confidence_out_of_range_is_rejected(self, tmp_path, monkeypatch):
        """I4 — confidence outside [0.0, 1.0] → recoverable Failure."""
        from pipelines.classify_extract import extract

        reply = json.dumps(
            [
                {
                    "action": "new",
                    "entity": "Anthony",
                    "tag": "other",
                    "fact": "Test.",
                    "confidence": 2.5,
                }
            ]
        )
        provider = _FakeProvider(reply=reply)
        monkeypatch.setattr(
            "pipelines.classify_extract.get_provider", lambda task, cfg: provider
        )

        result = await extract(
            dimension="TestDim",
            text="Some text.",
            existing_facts=[],
            guidance="Tag: other",
            feedback="",
            config=_make_config(str(tmp_path)),
        )

        assert isinstance(result, Failure)
        assert result.recoverable is True

    async def test_empty_entity_tag_or_fact_is_rejected(self, tmp_path, monkeypatch):
        """M5 — empty string for entity, tag, or fact → recoverable Failure."""
        from pipelines.classify_extract import extract

        for bad_field, bad_value in [("entity", ""), ("tag", ""), ("fact", "")]:
            item = {
                "action": "new",
                "entity": "Anthony",
                "tag": "other",
                "fact": "Test.",
                "confidence": 0.9,
            }
            item[bad_field] = bad_value
            reply = json.dumps([item])
            provider = _FakeProvider(reply=reply)
            monkeypatch.setattr(
                "pipelines.classify_extract.get_provider", lambda task, cfg: provider
            )

            result = await extract(
                dimension="TestDim",
                text="Some text.",
                existing_facts=[],
                guidance="Tag: other",
                feedback="",
                config=_make_config(str(tmp_path)),
            )

            assert isinstance(result, Failure), (
                f"Empty {bad_field!r} should be rejected"
            )
            assert result.recoverable is True

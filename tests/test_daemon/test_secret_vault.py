"""
tests/test_daemon/test_secret_vault.py

Tests for src/daemon/secret_vault.py — Secret Vault wrapper using keyring.

All tests use monkeypatch on keyring.get_password/set_password/delete_password
with an in-memory dict — do NOT touch the real OS vault.
"""

from __future__ import annotations

import os
from pathlib import Path
import keyring
import pytest

from daemon.secret_vault import delete_key, load_key_into_env, read_key, store_key


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def fake_keyring(monkeypatch) -> dict[tuple[str, str], str]:
    """Replace keyring functions with an in-memory dict backend.

    Returns the shared store dict so tests can pre-populate it directly.
    """
    store: dict[tuple[str, str], str] = {}

    def fake_set_password(service: str, username: str, password: str) -> None:
        store[(service, username)] = password

    def fake_get_password(service: str, username: str) -> str | None:
        return store.get((service, username))

    def fake_delete_password(service: str, username: str) -> None:
        store.pop((service, username), None)

    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    return store


# ── Tracer bullet: store-then-read round-trip ──────────────────────────

def test_store_then_read_round_trips(fake_keyring) -> None:
    """A key written by store_key is readable by read_key."""
    result = store_key("my-secret-api-key")
    assert result.is_success(), f"expected Success, got {result}"

    result = read_key()
    assert result.is_success(), f"expected Success, got {result}"
    assert result.unwrap() == "my-secret-api-key"


# ── Read-when-absent returns Failure ───────────────────────────────────

def test_read_key_when_absent_returns_failure(fake_keyring) -> None:
    """Calling read_key when no key has been stored returns a Failure."""
    result = read_key()
    assert result.is_failure(), f"expected Failure, got {result}"


# ── load_key_into_env sets the env slot ────────────────────────────────

def test_load_key_into_env_sets_environment_variable(
    fake_keyring, monkeypatch
) -> None:
    """After load_key_into_env, os.environ['KMS_DAEMON_API_KEY'] holds the key."""
    # Ensure env var is not already set
    monkeypatch.delenv("KMS_DAEMON_API_KEY", raising=False)

    store_key("env-test-key")
    result = load_key_into_env()
    assert result.is_success(), f"expected Success, got {result}"
    assert os.environ["KMS_DAEMON_API_KEY"] == "env-test-key"


# ── delete_key then read returns Failure ───────────────────────────────

def test_delete_key_then_read_returns_failure(fake_keyring) -> None:
    """After delete_key, a subsequent read_key returns Failure."""
    store_key("delete-me-key")

    delete_result = delete_key()
    assert delete_result.is_success(), f"expected Success, got {delete_result}"

    read_result = read_key()
    assert read_result.is_failure(), (
        f"expected Failure after delete, got {read_result}"
    )


# ── Grep-guard: no load_dotenv in secret_vault.py (C-11) ──────────────

def test_secret_vault_contains_no_load_dotenv() -> None:
    """secret_vault.py must not contain load_dotenv (Constraint C-11)."""
    src = Path(__file__).parents[2] / "src" / "daemon" / "secret_vault.py"
    text = src.read_text(encoding="utf-8")

    assert "load_dotenv" not in text, (
        "secret_vault.py contains load_dotenv — violates C-11. "
        "Use os.environ directly instead."
    )


# ── delete_key idempotency ─────────────────────────────────────────────

def test_delete_key_when_absent_is_idempotent(fake_keyring) -> None:
    """Calling delete_key when no key exists returns Success (idempotent)."""
    result = delete_key()
    assert result.is_success(), f"expected Success on absent key, got {result}"


# ── load_key_into_env failure path (empty vault) ────────────────────────

def test_load_key_into_env_when_vault_empty_returns_failure(
    fake_keyring, monkeypatch
) -> None:
    """load_key_into_env propagates Failure from read_key when vault is empty."""
    monkeypatch.delenv("KMS_DAEMON_API_KEY", raising=False)
    result = load_key_into_env()
    assert result.is_failure(), f"expected Failure on empty vault, got {result}"


# ── Backend exception paths ─────────────────────────────────────────────

def test_store_key_backend_failure_returns_failure(monkeypatch) -> None:
    """store_key returns Failure when keyring raises an exception."""
    def _raise(*args, **kwargs):
        raise RuntimeError("simulated backend failure")

    monkeypatch.setattr(keyring, "set_password", _raise)
    result = store_key("any-key")
    assert result.is_failure(), f"expected Failure, got {result}"


def test_read_key_backend_failure_returns_failure(monkeypatch) -> None:
    """read_key returns Failure when keyring raises an exception."""
    def _raise(*args, **kwargs):
        raise RuntimeError("simulated backend failure")

    monkeypatch.setattr(keyring, "get_password", _raise)
    result = read_key()
    assert result.is_failure(), f"expected Failure, got {result}"

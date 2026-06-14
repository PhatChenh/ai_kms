"""
tests/test_daemon/test_wizard.py

Tests for daemon/wizard.py — the save/test orchestration.
Tests the attempt_save function with stubbed check and store.
Tkinter widgets themselves are NOT unit-tested (GUI is manual-verify).
"""

from __future__ import annotations

import yaml

from core.result import Failure, Result, Success


# ── We import attempt_save after it exists; for now define the expected
#    interface and write tests that will drive the implementation.
# ── The DEFAULT_ENDPOINT constant will be in daemon.defaults.

# We test the orchestration function in-process by injecting stubs.


# ── Helpers ──────────────────────────────────────────────────────────────


def _stub_check_success(endpoint: str, key: str) -> Result[None]:
    """A stub connection check that always passes."""
    return Success(None)


def _stub_check_fail(error_msg: str = "authentication failed: bad key"):
    """Return a stub connection check that always fails."""

    def _check(endpoint: str, key: str) -> Result[None]:
        return Failure(
            error=error_msg,
            recoverable=False,
            context={"status_code": 401},
        )

    return _check


def _stub_store_success(key: str) -> Result[None]:
    """A stub key store that always succeeds."""
    return Success(None)


# ── Tracer bullet: check PASS → config written + key stored ────────────


def test_check_pass_writes_config_and_stores_key(tmp_path, monkeypatch):
    """When check succeeds, config YAML is written and key is stored."""
    # The test drives attempt_save directly — Tkinter is never touched.
    from daemon.wizard import attempt_save

    config_dir = tmp_path / ".kms-daemon"
    config_path = config_dir / "config.yaml"

    # Fix the config path for the test
    import daemon.wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_get_config_path", lambda: config_path)

    store_calls = []

    def tracking_store(key: str) -> Result[None]:
        store_calls.append(key)
        return Success(None)

    result = attempt_save(
        folder=tmp_path / "vault",
        endpoint="http://custom-endpoint.example.com",
        key="test-api-key-123",
        check=_stub_check_success,
        store=tracking_store,
    )

    # ── Result is Success
    assert result.is_success(), f"expected Success, got {result}"

    # ── Config file was written
    assert config_path.exists(), f"expected config file at {config_path}"

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw == {
        "vault_root": str(tmp_path / "vault"),
        "cloud_endpoint": "http://custom-endpoint.example.com",
    }, f"config YAML shape mismatch: {raw}"

    # ── Key was stored
    assert len(store_calls) == 1
    assert store_calls[0] == "test-api-key-123"


# ── check FAIL → nothing written ────────────────────────────────────────


def test_check_fail_writes_nothing(tmp_path, monkeypatch):
    """When check fails, no config is written and no key is stored."""
    from daemon.wizard import attempt_save

    config_dir = tmp_path / ".kms-daemon"
    config_path = config_dir / "config.yaml"

    import daemon.wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_get_config_path", lambda: config_path)

    store_called = False

    def tracking_store(key: str) -> Result[None]:
        nonlocal store_called
        store_called = True
        return Success(None)

    result = attempt_save(
        folder=tmp_path / "vault",
        endpoint="http://bad.example.com",
        key="bad-key",
        check=_stub_check_fail("authentication failed: invalid API key"),
        store=tracking_store,
    )

    # ── Result is Failure
    assert result.is_failure(), f"expected Failure, got {result}"
    assert "authentication" in result.error.lower()

    # ── No config file written
    assert not config_path.exists(), (
        f"config file should NOT exist after failed check, but found at {config_path}"
    )

    # ── No key stored
    assert not store_called, "key should NOT be stored when check fails"


# ── Edited endpoint flows through ────────────────────────────────────────


def test_edited_endpoint_flows_through(tmp_path, monkeypatch):
    """The endpoint written to config is the user-edited value, not the default."""
    from daemon.wizard import attempt_save

    config_dir = tmp_path / ".kms-daemon"
    config_path = config_dir / "config.yaml"

    import daemon.wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_get_config_path", lambda: config_path)

    store_calls = []

    def tracking_store(key: str) -> Result[None]:
        store_calls.append(key)
        return Success(None)

    # User edits the pre-filled default to something else
    custom_endpoint = "https://my-company-cloud.example.com/api"

    result = attempt_save(
        folder=tmp_path / "notes",
        endpoint=custom_endpoint,
        key="key-abc",
        check=_stub_check_success,
        store=tracking_store,
    )

    assert result.is_success()

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["cloud_endpoint"] == custom_endpoint, (
        f"expected custom endpoint in config, got {raw['cloud_endpoint']}"
    )


# ── Config file is loadable by DaemonConfig ─────────────────────────────


def test_written_config_is_loadable_by_daemon_config(tmp_path, monkeypatch):
    """The YAML written by attempt_save can be loaded by load_daemon_config."""
    from daemon.wizard import attempt_save

    config_dir = tmp_path / ".kms-daemon"
    config_path = config_dir / "config.yaml"

    import daemon.wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_get_config_path", lambda: config_path)

    # load_daemon_config reads KMS_DAEMON_API_KEY from env — set one
    monkeypatch.setenv("KMS_DAEMON_API_KEY", "env-key-xyz")

    vault_root = tmp_path / "my-vault"
    vault_root.mkdir(parents=True, exist_ok=True)

    result = attempt_save(
        folder=vault_root,
        endpoint="http://localhost:8080",
        key="env-key-xyz",
        check=_stub_check_success,
        store=_stub_store_success,
    )

    assert result.is_success()

    # Now load it
    from daemon.config import load_daemon_config

    cfg = load_daemon_config(config_path)
    assert cfg.vault_root == vault_root
    assert cfg.cloud_endpoint == "http://localhost:8080"
    assert cfg.api_key == "env-key-xyz"


# ── Config directory is created if missing ───────────────────────────────


def test_config_directory_created_if_missing(tmp_path, monkeypatch):
    """attempt_save creates the .kms-daemon directory if it doesn't exist."""
    from daemon.wizard import attempt_save

    config_dir = tmp_path / ".kms-daemon"
    config_path = config_dir / "config.yaml"

    import daemon.wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_get_config_path", lambda: config_path)

    # Directory does NOT exist yet
    assert not config_dir.exists()

    result = attempt_save(
        folder=tmp_path / "vault",
        endpoint="http://example.com",
        key="key123",
        check=_stub_check_success,
        store=_stub_store_success,
    )

    assert result.is_success()
    assert config_dir.exists()
    assert config_path.exists()


# ── Store failure after check success still writes config? ──────────────
# Per the spec: on check pass, write config THEN store key.
# If store fails, the config is already written — but the result should
# still reflect the failure (the user can re-run and the config file
# already exists, so the wizard would skip on next launch).


def test_store_failure_after_check_pass_propagates_error(tmp_path, monkeypatch):
    """If store fails after a passing check, the error is surfaced."""
    from daemon.wizard import attempt_save

    config_dir = tmp_path / ".kms-daemon"
    config_path = config_dir / "config.yaml"

    import daemon.wizard as wizard_mod

    monkeypatch.setattr(wizard_mod, "_get_config_path", lambda: config_path)

    def failing_store(key: str) -> Result[None]:
        return Failure(
            error="keyring backend unavailable",
            recoverable=False,
            context={},
        )

    result = attempt_save(
        folder=tmp_path / "vault",
        endpoint="http://example.com",
        key="key123",
        check=_stub_check_success,
        store=failing_store,
    )

    assert result.is_failure()
    assert "keyring" in result.error.lower()

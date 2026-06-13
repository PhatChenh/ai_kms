"""
tests/test_daemon/test_config.py

Comprehensive tests for daemon/config.py — DaemonConfig model + load_daemon_config.

Test map:
  Section 1 — DaemonConfig direct construction (field validators)
  Section 2 — Default values
  Section 3 — api_key exclusion from serialisation
  Section 4 — load_daemon_config (env-var injection, YAML parsing)
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from daemon.config import DaemonConfig, load_daemon_config


# ===========================================================================
# Helpers
# ===========================================================================


def _tmp_yaml(tmp_path: Path, content: str) -> Path:
    """Write *content* to a temp YAML file and return its path."""
    p = tmp_path / "config.yaml"
    p.write_text(content)
    return p


def _set_env(monkeypatch, key: str, value: str | None) -> None:
    """Set or delete an environment variable for the test duration."""
    if value is None:
        monkeypatch.delenv(key, raising=False)
    else:
        monkeypatch.setenv(key, value)


# ===========================================================================
# Section 1 — DaemonConfig field validators
# ===========================================================================


class TestVaultRootValidator:
    """@field_validator("vault_root") — must exist and be a directory."""

    def test_nonexistent_path_raises_validation_error(self):
        """A vault_root that doesn't exist on disk must fail."""
        with pytest.raises(ValidationError) as exc_info:
            DaemonConfig(
                vault_root=Path("/nonexistent/path/12345"),
                cloud_endpoint="http://localhost:8080",
                api_key="sk-test",
            )
        errors = exc_info.value.errors()
        assert any("vault_root" in str(e["loc"]) for e in errors)

    def test_file_instead_of_directory_raises(self, tmp_path: Path):
        """A regular file passed as vault_root must fail."""
        f = tmp_path / "not_a_dir"
        f.write_text("hello")
        with pytest.raises(ValidationError) as exc_info:
            DaemonConfig(
                vault_root=f,
                cloud_endpoint="http://localhost:8080",
                api_key="sk-test",
            )
        errors = exc_info.value.errors()
        assert any(
            "not a directory" in e["msg"].lower() for e in errors
        )

    def test_existing_directory_validates(self, tmp_path: Path):
        """A real directory passes validation."""
        cfg = DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="http://localhost:8080",
            api_key="sk-test",
        )
        assert cfg.vault_root == tmp_path


class TestCloudEndpointValidator:
    """@field_validator("cloud_endpoint") — must be non-empty."""

    def test_empty_string_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            DaemonConfig(
                vault_root=Path(__file__).parent,  # exists
                cloud_endpoint="",
                api_key="sk-test",
            )
        errors = exc_info.value.errors()
        assert any("cloud_endpoint" in str(e["loc"]) for e in errors)

    def test_whitespace_only_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            DaemonConfig(
                vault_root=Path(__file__).parent,
                cloud_endpoint="   ",
                api_key="sk-test",
            )
        errors = exc_info.value.errors()
        assert any("cloud_endpoint" in str(e["loc"]) for e in errors)

    def test_non_empty_string_passes(self, tmp_path: Path):
        cfg = DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="https://kms.example.com",
            api_key="sk-test",
        )
        assert cfg.cloud_endpoint == "https://kms.example.com"

    def test_whitespace_is_stripped(self, tmp_path: Path):
        """Leading/trailing whitespace should be stripped."""
        cfg = DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="  http://localhost:3838  ",
            api_key="sk-test",
        )
        assert cfg.cloud_endpoint == "http://localhost:3838"

    def test_ip_address_accepted(self, tmp_path: Path):
        """IP addresses and localhost are valid endpoints."""
        cfg = DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="http://192.168.1.1:8080",
            api_key="sk-test",
        )
        assert cfg.cloud_endpoint == "http://192.168.1.1:8080"


# ===========================================================================
# Section 2 — Default values
# ===========================================================================


class TestDefaults:
    """Verify every optional field's default."""

    @pytest.fixture
    def minimal_cfg(self, tmp_path: Path) -> DaemonConfig:
        return DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="http://localhost:8080",
            api_key="sk-test",
        )

    def test_debounce_seconds_default(self, minimal_cfg: DaemonConfig):
        assert minimal_cfg.debounce_seconds == 1.0

    def test_upload_concurrency_default(self, minimal_cfg: DaemonConfig):
        assert minimal_cfg.upload_concurrency == 4

    def test_retry_max_default(self, minimal_cfg: DaemonConfig):
        assert minimal_cfg.retry_max == 3

    def test_scan_batch_size_default(self, minimal_cfg: DaemonConfig):
        assert minimal_cfg.scan_batch_size == 50

    def test_max_file_size_bytes_default(self, minimal_cfg: DaemonConfig):
        assert minimal_cfg.max_file_size_bytes == 50_000_000

    def test_ignore_patterns_default(self, minimal_cfg: DaemonConfig):
        patterns = minimal_cfg.ignore_patterns
        assert ".git" in patterns
        assert ".obsidian" in patterns
        assert ".DS_Store" in patterns
        assert ".trash" in patterns
        assert ".stversions" in patterns
        assert "Thumbs.db" in patterns
        assert "~$*" in patterns
        assert "*.tmp" in patterns
        assert "*.swp" in patterns
        assert ".~lock*" in patterns
        # Default list should be exactly 10 entries
        assert len(patterns) == 10


# ===========================================================================
# Section 3 — api_key exclusion from serialisation
# ===========================================================================


class TestApiKeyExclusion:
    """api_key must never appear in YAML or dict output."""

    def test_model_dump_excludes_api_key(self, tmp_path: Path):
        cfg = DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="http://localhost:8080",
            api_key="sk-secret-123",
        )
        dumped = cfg.model_dump()
        assert "api_key" not in dumped
        assert "vault_root" in dumped
        assert "cloud_endpoint" in dumped

    def test_model_dump_json_excludes_api_key(self, tmp_path: Path):
        cfg = DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="http://localhost:8080",
            api_key="sk-secret-123",
        )
        json_str = cfg.model_dump_json()
        assert "api_key" not in json_str
        assert "sk-secret-123" not in json_str

    def test_api_key_still_accessible_as_attribute(self, tmp_path: Path):
        """Exclusion from dump doesn't mean the field is inaccessible."""
        cfg = DaemonConfig(
            vault_root=tmp_path,
            cloud_endpoint="http://localhost:8080",
            api_key="sk-secret-123",
        )
        assert cfg.api_key == "sk-secret-123"


# ===========================================================================
# Section 4 — load_daemon_config
# ===========================================================================


class TestLoadDaemonConfig:
    """Integration tests for load_daemon_config()."""

    def test_reads_yaml_and_injects_api_key(self, tmp_path: Path, monkeypatch):
        """Happy path: YAML has vault_root + cloud_endpoint, env has API key."""
        yaml_path = _tmp_yaml(
            tmp_path,
            f"vault_root: {tmp_path}\ncloud_endpoint: http://localhost:8080\n",
        )
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-env-key")

        cfg = load_daemon_config(yaml_path)

        assert cfg.vault_root == tmp_path
        assert cfg.cloud_endpoint == "http://localhost:8080"
        assert cfg.api_key == "sk-env-key"

    def test_api_key_from_env_overrides_yaml(self, tmp_path: Path, monkeypatch):
        """Even if YAML contains api_key, the env var wins."""
        yaml_path = _tmp_yaml(
            tmp_path,
            (
                f"vault_root: {tmp_path}\n"
                "cloud_endpoint: http://localhost:8080\n"
                "api_key: sk-from-yaml\n"
            ),
        )
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-from-env")

        cfg = load_daemon_config(yaml_path)

        assert cfg.api_key == "sk-from-env", (
            "API key must come from env, not YAML"
        )

    def test_raises_when_env_var_not_set(self, tmp_path: Path, monkeypatch):
        """Without KMS_DAEMON_API_KEY, load_daemon_config must raise."""
        yaml_path = _tmp_yaml(
            tmp_path,
            f"vault_root: {tmp_path}\ncloud_endpoint: http://localhost:8080\n",
        )
        # Explicitly remove the env var
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", None)

        with pytest.raises(ValueError, match="KMS_DAEMON_API_KEY"):
            load_daemon_config(yaml_path)

    def test_raises_when_env_var_is_empty(self, tmp_path: Path, monkeypatch):
        """An empty string is also invalid."""
        yaml_path = _tmp_yaml(
            tmp_path,
            f"vault_root: {tmp_path}\ncloud_endpoint: http://localhost:8080\n",
        )
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "")

        with pytest.raises(ValueError, match="KMS_DAEMON_API_KEY"):
            load_daemon_config(yaml_path)

    def test_custom_yaml_path(self, tmp_path: Path, monkeypatch):
        """A custom path is respected."""
        custom = tmp_path / "custom.yaml"
        custom.write_text(
            f"vault_root: {tmp_path}\ncloud_endpoint: https://other.example.com\n"
        )
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-custom")

        cfg = load_daemon_config(custom)

        assert cfg.cloud_endpoint == "https://other.example.com"

    def test_missing_yaml_at_default_path_uses_env(self, tmp_path: Path, monkeypatch):
        """If the default YAML doesn't exist, we still try to build the config.
        vault_root and cloud_endpoint are required by the model, so this
        will raise ValidationError — but that's better than a cryptic
        FileNotFoundError."""
        # Point HOME to a temp dir with no config
        monkeypatch.setenv("HOME", str(tmp_path))
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-test")

        # No YAML file exists — validation will fail on missing vault_root
        with pytest.raises(ValidationError):
            load_daemon_config()

    def test_extra_fields_forbidden(self, tmp_path: Path, monkeypatch):
        """Unknown keys in YAML must cause validation to fail."""
        yaml_path = _tmp_yaml(
            tmp_path,
            (
                f"vault_root: {tmp_path}\n"
                "cloud_endpoint: http://localhost:8080\n"
                "made_up_field: 42\n"
            ),
        )
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-test")

        with pytest.raises(ValidationError):
            load_daemon_config(yaml_path)

    def test_all_optional_fields_can_be_set_in_yaml(self, tmp_path: Path, monkeypatch):
        """Every optional field can be overridden via YAML."""
        yaml_path = _tmp_yaml(
            tmp_path,
            (
                f"vault_root: {tmp_path}\n"
                "cloud_endpoint: http://localhost:8080\n"
                "debounce_seconds: 2.5\n"
                "ignore_patterns:\n"
                "  - .git\n"
                "  - .obsidian\n"
                "  - node_modules\n"
                "upload_concurrency: 8\n"
                "retry_max: 5\n"
                "scan_batch_size: 100\n"
                "max_file_size_bytes: 10000000\n"
            ),
        )
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-test")

        cfg = load_daemon_config(yaml_path)

        assert cfg.debounce_seconds == 2.5
        assert cfg.ignore_patterns == [".git", ".obsidian", "node_modules"]
        assert cfg.upload_concurrency == 8
        assert cfg.retry_max == 5
        assert cfg.scan_batch_size == 100
        assert cfg.max_file_size_bytes == 10_000_000

    def test_invalid_yaml_syntax_raises(self, tmp_path: Path, monkeypatch):
        """Broken YAML must surface as yaml.YAMLError."""
        yaml_path = _tmp_yaml(tmp_path, "vault_root: [unclosed\n  sub: value\n")
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-test")

        with pytest.raises(yaml.YAMLError):
            load_daemon_config(yaml_path)

    def test_empty_yaml_file_with_required_fields_injects_api_key(
        self, tmp_path: Path, monkeypatch
    ):
        """An empty YAML is treated as {} — required fields fail validation."""
        yaml_path = _tmp_yaml(tmp_path, "")
        _set_env(monkeypatch, "KMS_DAEMON_API_KEY", "sk-test")

        with pytest.raises(ValidationError):
            load_daemon_config(yaml_path)

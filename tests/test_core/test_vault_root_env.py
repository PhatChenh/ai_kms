"""
tests/test_core/test_vault_root_env.py

TD-059: VAULT_ROOT environment variable override for container boot.

Tests that VAULT_ROOT env var injects into raw_main dict **pre-construction**
so validate_vault_root_exists does not crash on a YAML-configured path
that does not exist in the container environment.

Design constraints (from task description):
- Do NOT import CONFIG at module scope — lazy-import load_config inside tests.
- Use monkeypatch.setenv / monkeypatch.delenv for env var control.
- Set env: prod or dev so select_vault_by_env does NOT redirect the root
  to testing.vault_path (which would mask the env-var override).

Test map:
  1. VAULT_ROOT set to an existing directory → cfg.main.vault.root == that dir.
     Proves the container boot path (P5-DEPLOY-10).
  2. VAULT_ROOT unset → YAML vault.root value used unchanged.
     Proves the local/stdio path is unaffected.
  3. VAULT_ROOT set to a non-existent directory → ConfigError raised.
     Proves the env var does NOT bypass existence validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.exceptions import ConfigError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    """Dump a dict to a YAML file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)


def _write_config_files(cfg_dir: Path, vault_root: Path, env: str) -> None:
    """Write minimal but valid config.yaml, thresholds.yaml, routing.yaml."""
    _write_yaml(cfg_dir / "config.yaml", {
        "vault": {
            "root": str(vault_root),
            "inbox_dir": "inbox",
            "projects_dir": "Projects",
            "domain_dir": "Domain",
            "documentation_dir": "Documentation",
            "synthesis_dir": "Synthesis",
            "briefings_dir": "Briefings",
            "archive_dir": "Archive",
        },
        "logging": {"level": "DEBUG", "console": True},
        "providers": {
            "classify": "claude",
            "synthesis": "claude",
            "embeddings": "ollama",
            "self_learn": "claude",
            "capture": "claude",
        },
        "claude": {
            "model": "claude-haiku-4-5-20251001",
            "synthesis_model": "claude-sonnet-20250514",
            "max_tokens": 1024,
            "timeout": 60,
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "chat_model": "llama3",
            "embedding_model": "nomic-embed-text",
            "timeout": 120,
            "delay_between_calls": 2,
        },
        "mcp": {
            "port": 3838,
            "host": "0.0.0.0",
            "enable_http": False,
        },
        "self_learning": {
            "enabled": True,
            "min_evaluations": 20,
            "confidence_threshold": 0.8,
            "include_examples_in_prompt": True,
            "max_examples": 5,
        },
        "env": env,
    })

    _write_yaml(cfg_dir / "thresholds.yaml", {
        "global": {"auto": 0.85, "suggest": 0.60},
        "pipelines": {},
    })

    _write_yaml(cfg_dir / "routing.yaml", {
        "pipelines": {},
    })


# ===========================================================================
# Tests
# ===========================================================================


class TestVaultRootEnv:
    """VAULT_ROOT env var injection — container boot path (P5-DEPLOY-10)."""

    # ── 1. VAULT_ROOT set → overrides YAML value ──────────────────────────

    def test_vault_root_env_overrides_yaml_value(
        self, tmp_path: Path, monkeypatch
    ):
        """
        When VAULT_ROOT is set to an existing directory, load_config() uses
        that path as cfg.main.vault.root — even when the YAML vault.root
        points to a non-existent path.

        This is the container boot path: /data/vault exists but config.yaml
        does not contain its path.
        """
        import core.config as cfg_module

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()

        # The env-var vault directory — exists.
        env_vault = tmp_path / "env_vault"
        env_vault.mkdir()

        # The YAML vault.root points to a path that does NOT exist.
        # Without VAULT_ROOT, this would crash at construction.
        yaml_vault = tmp_path / "yaml_vault_does_not_exist"
        # Intentionally do NOT create this directory.

        _write_config_files(cfg_dir, yaml_vault, env="prod")

        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", cfg_dir)
        monkeypatch.setenv("VAULT_ROOT", str(env_vault))

        cfg = cfg_module.load_config()

        # The env var wins.
        assert cfg.main.vault.root == env_vault
        assert cfg.main.vault.root.exists()
        assert cfg.main.vault.root.is_dir()

    # ── 2. VAULT_ROOT unset → YAML value used unchanged ───────────────────

    def test_vault_root_unset_uses_yaml_value(
        self, tmp_path: Path, monkeypatch
    ):
        """
        When VAULT_ROOT is NOT set, the YAML vault.root value is used
        unchanged. This proves the local/stdio path is unaffected by the
        env-var mechanism.
        """
        import core.config as cfg_module

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()

        yaml_vault = tmp_path / "yaml_vault"
        yaml_vault.mkdir()

        _write_config_files(cfg_dir, yaml_vault, env="dev")

        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", cfg_dir)
        monkeypatch.delenv("VAULT_ROOT", raising=False)

        cfg = cfg_module.load_config()

        assert cfg.main.vault.root == yaml_vault

    # ── 3. VAULT_ROOT points to non-existent dir → ConfigError ────────────

    def test_vault_root_env_missing_dir_still_validates(
        self, tmp_path: Path, monkeypatch
    ):
        """
        If VAULT_ROOT points to a non-existent directory, the validator
        (validate_vault_root_exists) must still catch it and raise
        ConfigError. The env var does NOT bypass validation.
        """
        import core.config as cfg_module

        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()

        yaml_vault = tmp_path / "yaml_vault"
        yaml_vault.mkdir()

        env_vault = tmp_path / "env_vault_does_not_exist"
        # Intentionally do NOT create this directory.

        _write_config_files(cfg_dir, yaml_vault, env="dev")

        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", cfg_dir)
        monkeypatch.setenv("VAULT_ROOT", str(env_vault))

        with pytest.raises(ConfigError):
            cfg_module.load_config()

"""
tests/test_core/conftest.py

Shared fixtures for all config-layer tests.

Design notes:
- `config_dir` builds a *complete* valid config directory in tmp_path.
  Every test that calls load_config() uses this so they never touch real files.
- Vault-related fixtures create an actual temp directory, because MainConfig's
  model_validator calls .exists() and .is_dir() at parse time.
- Env-var fixtures clean up after themselves via monkeypatch, so tests never
  bleed API-key state into each other.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    """Dump a dict to a YAML file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False)


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    """
    A real, empty directory that satisfies MainConfig's vault-root validator.
    Tests that need a vault path use this, so the validator doesn't blow up.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    return vault


@pytest.fixture()
def config_dir(tmp_path: Path, vault_dir: Path) -> Path:
    """
    A temporary config/ directory that contains all three YAML files with
    valid, minimal content. Pass this to load_config() via monkeypatching
    core.config._CONFIG_DIR.

    Returns the Path to the directory (not to individual files).
    """
    cfg = tmp_path / "config"
    cfg.mkdir()

    # -- config.yaml ----------------------------------------------------------
    _write_yaml(cfg / "config.yaml", {
        "vault": {
            "root": str(vault_dir),
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
            "synthesis_model": "claude-sonnet-4-20250514",
            "max_tokens": 1024,
            "timeout": 60,
        },
        "ollama": {
            "base_url": "http://localhost:11434",
            "chat_model": "qwen3.5:9b",
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
        "env": "dev",
    })

    # -- thresholds.yaml -------------------------------------------------------
    _write_yaml(cfg / "thresholds.yaml", {
        "global": {"auto": 0.85, "review": 0.60},
        "pipelines": {
            "classify": {"auto": 0.90, "review": 0.70},
        },
    })

    # -- routing.yaml ----------------------------------------------------------
    _write_yaml(cfg / "routing.yaml", {
        "pipelines": {},
    })

    return cfg


@pytest.fixture()
def loaded_config(monkeypatch, config_dir: Path):
    """
    A fully-loaded Config object backed by the temporary config_dir fixture.
    Use this in tests that need a real Config without touching real files.
    """
    import core.config as cfg_module
    monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)
    return cfg_module.load_config()


# ---------------------------------------------------------------------------
# API-key env-var fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def with_anthropic_key(monkeypatch):
    """Set a fake Anthropic API key for the duration of the test."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123")
    yield "sk-ant-test-key-123"
    # monkeypatch automatically restores the env on teardown


@pytest.fixture()
def without_api_keys(monkeypatch):
    """Ensure neither API key is set (for testing None defaults)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield
"""
tests/test_mcp_server/conftest.py

Shared fixtures for MCP server tests.
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
# Config setup — so CONFIG import inside _bootstrap doesn't fail
# ---------------------------------------------------------------------------


@pytest.fixture()
def vault_dir(tmp_path: Path) -> Path:
    """A real, empty directory that satisfies MainConfig's vault-root validator."""
    vault = tmp_path / "vault"
    vault.mkdir()
    # Create minimal subdirs
    (vault / "inbox").mkdir()
    (vault / "Projects").mkdir()
    (vault / "Domain").mkdir()
    return vault


@pytest.fixture()
def config_dir(tmp_path: Path, vault_dir: Path) -> Path:
    """A temporary config/ directory with minimal valid YAML files."""
    cfg = tmp_path / "config"
    cfg.mkdir()

    _write_yaml(
        cfg / "config.yaml",
        {
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
                "chat_model": "llama3",
                "embedding_model": "nomic-embed-text",
                "timeout": 120,
                "delay_between_calls": 2,
            },
            "mcp": {
                "port": 3838,
                "host": "0.0.0.0",
                "enable_http": False,
                "context_injection": {
                    "max_entities_per_dimension": 15,
                    "max_orientation_facts_per_dimension": 5,
                },
            },
            "self_learning": {
                "enabled": True,
                "min_evaluations": 20,
                "confidence_threshold": 0.8,
                "include_examples_in_prompt": True,
                "max_examples": 5,
            },
            "env": "dev",
        },
    )

    _write_yaml(
        cfg / "thresholds.yaml",
        {
            "global": {"auto": 0.85, "suggest": 0.60},
            "pipelines": {
                "classify": {"auto": 0.90, "suggest": 0.70},
            },
        },
    )

    _write_yaml(
        cfg / "routing.yaml",
        {
            "pipelines": {},
        },
    )

    return cfg


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch, config_dir: Path):
    """Ensure every MCP server test has a valid config path and API key env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-mcp")
    import core.config as cfg_module

    monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)
    monkeypatch.setattr(cfg_module, "_CONFIG", None)

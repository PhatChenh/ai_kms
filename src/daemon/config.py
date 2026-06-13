"""
daemon/config.py

Standalone daemon configuration.  Zero imports from core/config — the daemon
must start without ever triggering cloud config validation (C-19).

Usage:
    from daemon.config import load_daemon_config, DaemonConfig

    cfg = load_daemon_config()                     # reads ~/.kms-daemon/config.yaml
    cfg = load_daemon_config(Path("/etc/kms.yaml"))  # custom path

The API key always comes from the environment variable ``KMS_DAEMON_API_KEY``,
never from the YAML file.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class DaemonConfig(BaseModel):
    """Configuration for the sync daemon.

    All fields except ``vault_root`` and ``cloud_endpoint`` have sensible
    defaults so a minimal YAML is only two lines.
    """

    model_config = {"extra": "forbid"}

    # ── required fields (no defaults) ────────────────────────────────────
    vault_root: Path
    cloud_endpoint: str

    # ── secrets (never serialised to YAML) ───────────────────────────────
    api_key: str = Field(exclude=True, repr=False)

    # ── optional fields with defaults ────────────────────────────────────
    debounce_seconds: float = 1.0
    ignore_patterns: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".obsidian",
            ".trash",
            ".stversions",
            ".DS_Store",
            "Thumbs.db",
            "~$*",
            "*.tmp",
            "*.swp",
            ".~lock*",
        ]
    )
    upload_concurrency: int = 4
    retry_max: int = 3
    scan_batch_size: int = 50
    max_file_size_bytes: int = 50_000_000  # 50 MB

    # ── validators ──────────────────────────────────────────────────────

    @field_validator("vault_root")
    @classmethod
    def _vault_root_must_exist(cls, v: Path) -> Path:
        """Fail fast if the vault path doesn't exist on disk."""
        if not v.exists():
            raise ValueError(f"vault_root does not exist: {v}")
        if not v.is_dir():
            raise ValueError(f"vault_root is not a directory: {v}")
        return v

    @field_validator("cloud_endpoint")
    @classmethod
    def _cloud_endpoint_not_empty(cls, v: str) -> str:
        """Reject empty or whitespace-only strings."""
        if not v or not v.strip():
            raise ValueError("cloud_endpoint must be a non-empty string")
        return v.strip()


def load_daemon_config(path: Path | None = None) -> DaemonConfig:
    """Load daemon configuration from a YAML file, injecting the API key from env.

    1. Read YAML from *path* (default: ``~/.kms-daemon/config.yaml``).
       If the file does not exist at the default path, proceed with an empty
       dict — the required fields ``vault_root`` and ``cloud_endpoint`` will
       be caught by Pydantic validation.
    2. Override (or set) ``api_key`` from the environment variable
       ``KMS_DAEMON_API_KEY``.
    3. Construct and validate ``DaemonConfig``.

    Raises:
        ValueError: if ``KMS_DAEMON_API_KEY`` is not set in the environment.
        pydantic.ValidationError: if any field fails validation.
        yaml.YAMLError: if the YAML file is syntactically invalid.
    """
    if path is None:
        path = Path.home() / ".kms-daemon" / "config.yaml"

    # Read YAML (tolerate missing file only at the default path)
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    # ── api_key: always from the environment, never from YAML ──────────
    api_key = os.environ.get("KMS_DAEMON_API_KEY")
    if not api_key:
        raise ValueError(
            "KMS_DAEMON_API_KEY environment variable is not set. "
            "The daemon requires an API key to authenticate with the cloud endpoint."
        )
    data["api_key"] = api_key

    return DaemonConfig(**data)

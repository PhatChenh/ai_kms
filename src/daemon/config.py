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
from pydantic import BaseModel, Field, field_validator, model_validator


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
    debounce_seconds: float = Field(default=1.0, gt=0)
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
    upload_concurrency: int = Field(default=4, ge=1)
    retry_max: int = Field(default=3, ge=1)
    scan_batch_size: int = Field(default=50, ge=1)
    max_file_size_bytes: int = Field(default=50_000_000, ge=0)  # 50 MB

    # ── Phase 2 (cache & reconcile) ─────────────────────────────────────
    cache_path: str = Field(default="~/.kms-daemon/cache.json", validate_default=True)
    move_window_seconds: float = Field(default=2.0)
    periodic_interval_seconds: int = Field(default=21600, ge=0)
    sweep_delete_confirmations: int = Field(default=2, ge=1)

    # ── validators ──────────────────────────────────────────────────────

    @field_validator("vault_root")
    @classmethod
    def _vault_root_must_exist(cls, v: Path) -> Path:
        """Fail fast if the vault path doesn't exist on disk."""
        try:
            if not v.exists():
                raise ValueError(f"vault_root does not exist: {v}")
            if not v.is_dir():
                raise ValueError(f"vault_root is not a directory: {v}")
            return v
        except PermissionError as exc:
            raise ValueError(f"cannot access vault_root: {v} ({exc})") from exc

    @field_validator("cloud_endpoint")
    @classmethod
    def _cloud_endpoint_not_empty(cls, v: str) -> str:
        """Reject empty or whitespace-only strings."""
        stripped = v.strip()
        if not stripped:
            raise ValueError("cloud_endpoint must be a non-empty string")
        return stripped

    @field_validator("cache_path")
    @classmethod
    def _expand_cache_path_tilde(cls, v: str) -> str:
        """Expand ~ to the user's home directory."""
        return str(Path(v).expanduser())

    @model_validator(mode="after")
    def _move_window_gt_debounce(self) -> "DaemonConfig":
        """Ensure move_window_seconds > debounce_seconds."""
        if self.move_window_seconds <= self.debounce_seconds:
            raise ValueError(
                f"move_window_seconds must be greater than debounce_seconds "
                f"(currently {self.debounce_seconds})"
            )
        return self


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
    is_default_path = path is None
    if is_default_path:
        path = Path.home() / ".kms-daemon" / "config.yaml"

    # ── Read YAML ──────────────────────────────────────────────────────
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        if raw is None:
            data = {}
        elif not isinstance(raw, dict):
            raise yaml.YAMLError("config YAML root must be a mapping")
        else:
            data = raw
    elif is_default_path:
        # Missing default path → empty dict (validators catch missing required fields).
        data = {}
    else:
        raise FileNotFoundError(f"config file not found: {path}")

    # ── api_key: always from the environment, never from YAML ──────────
    api_key = os.environ.get("KMS_DAEMON_API_KEY")
    if not api_key:
        raise ValueError(
            "KMS_DAEMON_API_KEY environment variable is not set. "
            "The daemon requires an API key to authenticate with the cloud endpoint."
        )
    data["api_key"] = api_key

    return DaemonConfig(**data)

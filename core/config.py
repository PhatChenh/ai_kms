"""
core/config.py

Single source of truth for all project configuration.

Usage (anywhere in the project):
    from core.config import CONFIG
    print(CONFIG.main.vault.root)

Never call load_config() outside this module.
The module-level CONFIG singleton is loaded once on first import.
"""

import logging
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Resolve config directory relative to this file, not the working directory.
# This means `python -m pipelines.foo` from any folder still finds the YAMLs.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"

# ---------------------------------------------------------------------------
# Type aliases — 3.12 `type` statement makes these runtime-enforced,
# not just annotation conventions.
# ---------------------------------------------------------------------------
type Provider = Literal["anthropic", "openai", "ollama"]
type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


# ===========================================================================
# 1. Sub-models for config/config.yaml
# ===========================================================================

class VaultConfig(BaseModel):
    root: Path
    journal_dir: str = "Journal"
    resources_dir: str = "Resources"
    inbox_dir: str = "Inbox"

    # Derived helpers — call these instead of string-building manually.
    @property
    def journal_path(self) -> Path:
        return self.root / self.journal_dir

    @property
    def resources_path(self) -> Path:
        return self.root / self.resources_dir

    @property
    def inbox_path(self) -> Path:
        return self.root / self.inbox_dir


class LoggingConfig(BaseModel):
    level: LogLevel = "INFO"
    file: str = "logs/app.log"
    console: bool = True


class LLMModels(BaseModel):
    anthropic: str = "claude-sonnet-4-20250514"
    openai: str = "gpt-4o"
    ollama: str = "llama3"


class LLMConfig(BaseModel):
    default_provider: Provider = "anthropic"
    models: LLMModels = Field(default_factory=LLMModels)
    timeout_seconds: int = 60


class DatabaseConfig(BaseModel):
    path: Path = Path("data/obsidian_tool.db")


class MainConfig(BaseModel):
    """Schema for config/config.yaml."""

    vault: VaultConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    env: Literal["dev", "prod"] = "dev"

    @model_validator(mode="after")
    def validate_vault_root_exists(self) -> Self:
        """Fail fast: crash at startup if the vault path is wrong."""
        if not self.vault.root.exists():
            raise ValueError(
                f"Vault root does not exist: {self.vault.root}\n"
                f"Check vault.root in config/config.yaml and make sure the path is correct."
            )
        if not self.vault.root.is_dir():
            raise ValueError(
                f"Vault root is not a directory: {self.vault.root}"
            )
        return self


# ===========================================================================
# 2. Sub-models for config/thresholds.yaml
# ===========================================================================

class ConfidenceBand(BaseModel):
    """A pair of (auto, review) cutoffs used by routing logic."""
    auto: float = Field(0.85, ge=0.0, le=1.0)
    review: float = Field(0.60, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def review_below_auto(self) -> Self:
        if self.review >= self.auto:
            raise ValueError(
                f"'review' threshold ({self.review}) must be strictly less than "
                f"'auto' threshold ({self.auto})."
            )
        return self


class Thresholds(BaseModel):
    """Schema for config/thresholds.yaml."""
    global_: ConfidenceBand = Field(default_factory=ConfidenceBand, alias="global")
    pipelines: dict[str, ConfidenceBand] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    def for_pipeline(self, pipeline_name: str) -> ConfidenceBand:
        """
        Return thresholds for a named pipeline, falling back to global.

        Usage:
            band = CONFIG.thresholds.for_pipeline("journal_tagger")
            if score >= band.auto:
                ...
        """
        return self.pipelines.get(pipeline_name, self.global_)


# ===========================================================================
# 3. Sub-models for config/routing.yaml
# ===========================================================================

class PipelineRouting(BaseModel):
    """Routing overrides for one pipeline. All fields optional."""
    provider: Provider | None = None
    model: str | None = None
    fallback_provider: Provider | None = None


class Routing(BaseModel):
    """Schema for config/routing.yaml. Currently empty — Phase 2 fills this."""
    pipelines: dict[str, PipelineRouting] = Field(default_factory=dict)


# ===========================================================================
# 4. API keys — environment variables only, never YAML
# ===========================================================================

class ApiKeys(BaseSettings):
    """
    Reads API keys from environment variables.
    Set these in your shell or .env file, never in YAML.

        export ANTHROPIC_API_KEY="sk-ant-..."
        export OPENAI_API_KEY="sk-..."

    Pydantic-settings will raise a clear error at startup if a required key
    is missing, rather than failing silently at first API call.
    """

    model_config = SettingsConfigDict(
        env_file=".env",          # optional: also reads from .env at project root
        env_file_encoding="utf-8",
        extra="ignore",           # ignore unrelated env vars
    )

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")

    @field_validator("anthropic_api_key", "openai_api_key", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: str | None) -> str | None:
        """Treat empty string the same as missing."""
        return v if v else None


# ===========================================================================
# 5. Composing model + loader
# ===========================================================================

class Config(BaseModel):
    """
    The single Config object exposed to the rest of the project.

    Access pattern:
        from core.config import CONFIG

        CONFIG.main.vault.root          → Path to vault
        CONFIG.main.env                 → "dev" or "prod"
        CONFIG.thresholds.for_pipeline("x")  → ConfidenceBand
        CONFIG.routing.pipelines        → dict of per-pipeline overrides
        CONFIG.keys.anthropic_api_key   → str | None
    """

    main: MainConfig
    thresholds: Thresholds
    routing: Routing
    keys: ApiKeys


def _load_yaml(filename: str) -> dict:
    """Read one YAML file from the config directory. Returns empty dict if file is empty."""
    path = _CONFIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Expected all three files: config.yaml, thresholds.yaml, routing.yaml"
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def load_config() -> Config:
    """
    Load and validate all config files. Called once at module level.

    Raises:
        FileNotFoundError  — if any YAML file is missing
        ValidationError    — if any value fails the schema (wrong type, bad path, etc.)
    """
    raw_main = _load_yaml("config.yaml")
    raw_thresholds = _load_yaml("thresholds.yaml")
    raw_routing = _load_yaml("routing.yaml")

    return Config(
        main=MainConfig(**raw_main),
        thresholds=Thresholds(**raw_thresholds),
        routing=Routing(**raw_routing),
        keys=ApiKeys(),  # reads from env / .env automatically
    )


# ===========================================================================
# 6. Singleton — the one line every other module imports
# ===========================================================================

try:
    CONFIG: Config = load_config()
    logging.getLogger(__name__).info(
        "Config loaded. env=%s vault=%s",
        CONFIG.main.env,
        CONFIG.main.vault.root,
    )
except Exception as exc:
    # Re-raise with a clear message so the first thing you see on startup
    # is exactly what went wrong, not a traceback from a random import site.
    raise RuntimeError(
        f"\n\n{'='*60}\n"
        f"CONFIG LOAD FAILED — fix this before running anything.\n"
        f"{'='*60}\n"
        f"{exc}\n"
    ) from exc
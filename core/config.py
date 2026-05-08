"""
core/config.py

Single source of truth for all project configuration.

Usage (anywhere in the project):
    from core.config import CONFIG

    CONFIG.main.vault.inbox_path          → Path to inbox folder
    CONFIG.main.claude.model              → fast model string
    CONFIG.main.providers.for_task("synthesis") → "claude" | "ollama"
    CONFIG.thresholds.for_pipeline("classify")  → ConfidenceBand
    CONFIG.keys.anthropic_api_key         → str | None

Never call load_config() outside this module.
The module-level CONFIG singleton is loaded once on first import.
"""

import logging
from pathlib import Path
from typing import Literal, Self
from enum import Enum

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.exceptions import ConfigError 

# ---------------------------------------------------------------------------
# Resolve config dir relative to this file — works regardless of cwd.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"

# ---------------------------------------------------------------------------
# Type aliases — 3.12 `type` statement, runtime-enforced.
# ---------------------------------------------------------------------------
type Provider  = Literal["claude", "ollama"]
type Task      = Literal["classify", "synthesis", "embeddings", "self_learn", "capture"]
type LogLevel  = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
type Env       = Literal["dev", "prod"]


# core/config.py

class RouteDecision(str, Enum):
    """
    The three possible outcomes of confidence-gated routing.

    AUTO     — score ≥ band.auto:    pipeline acts immediately, no human needed.
    SUGGEST  — score ≥ band.suggest: AI has candidate destinations but is not
               confident enough to act. Flags the note with suggestions for
               human confirmation before any move is made.
    CLUELESS — score <  band.suggest: AI reviewed the note but could not form
               a useful candidate. Flags the note with a "needs human"
               signal. Never stays silent — the audit trail always records
               the review attempt.
    """
    AUTO     = "auto"
    SUGGEST  = "suggest"
    CLUELESS = "clueless"

# ===========================================================================
# 1. Sub-models for config/config.yaml
# ===========================================================================

class VaultConfig(BaseModel):
    """
    Vault root + all sub-folder names.
    Add a new folder here + a matching @property — nothing else changes.
    """
    root:             Path
    inbox_dir:         str = "inbox"
    projects_dir:      str = "Projects"
    domain_dir:        str = "Domain"
    documentation_dir: str = "Documentation"
    synthesis_dir:     str = "Synthesis"
    briefings_dir:     str = "Briefings"
    archive_dir:       str = "Archive"

    # ── derived path helpers ──────────────────────────────────────────────
    # Always use these; never build paths by string concatenation elsewhere.
    @property
    def inbox_path(self)         -> Path: return self.root / self.inbox_dir
    @property
    def projects_path(self)      -> Path: return self.root / self.projects_dir
    @property
    def domain_path(self)        -> Path: return self.root / self.domain_dir
    @property
    def documentation_path(self) -> Path: return self.root / self.documentation_dir
    @property
    def synthesis_path(self)     -> Path: return self.root / self.synthesis_dir
    @property
    def briefings_path(self)     -> Path: return self.root / self.briefings_dir
    @property
    def archive_path(self)       -> Path: return self.root / self.archive_dir


class DatabaseConfig(BaseModel):
    path: Path = Path("./data/kb.db")


class ProvidersConfig(BaseModel):
    """
    Per-task provider selection. Maps each pipeline task to "claude" or "ollama".
    Adding a new task = add a field here + add the task to the Task type alias.
    """
    classify:   Provider = "claude"
    synthesis:  Provider = "claude"
    embeddings: Provider = "ollama"
    self_learn: Provider = "claude"
    capture:    Provider = "claude"

    def for_task(self, task: Task) -> Provider:
        """
        Return the configured provider for a task.

        Usage in a pipeline:
            provider_name = CONFIG.main.providers.for_task("classify")
            provider = get_provider(provider_name, CONFIG.main)
        """
        return getattr(self, task)


class ClaudeConfig(BaseModel):
    """
    Claude-specific settings.
    Two models: a fast/cheap one for most tasks, a smarter one for synthesis.
    """
    model:           str = "claude-haiku-4-5-20251001"   # capture, classify, self_learn
    synthesis_model: str = "claude-sonnet-4-20250514"    # synthesis, documentation
    max_tokens:      int = 1024
    timeout:         int = 60   # seconds


class OllamaConfig(BaseModel):
    """Ollama local server settings."""
    base_url:            str = "http://localhost:11434"
    chat_model:          str = "qwen3.5:9b"
    embedding_model:     str = "nomic-embed-text"
    timeout:             int = 120
    delay_between_calls: int = 2   # seconds between batch calls


class MCPConfig(BaseModel):
    """MCP server settings (Roadmap 9)."""
    port:        int  = 3838
    host:        str  = "0.0.0.0"
    enable_http: bool = False


class SelfLearningConfig(BaseModel):
    """Controls how the self-learning pipeline adjusts prompts (Roadmap 8)."""
    enabled:                    bool  = True
    min_evaluations:            int   = 20
    confidence_threshold:       float = Field(0.8, ge=0.0, le=1.0)
    include_examples_in_prompt: bool  = True
    max_examples:               int   = 5


class LoggingConfig(BaseModel):
    level:   LogLevel = "INFO"
    file:    str      = "logs/app.log"
    console: bool     = True


class MainConfig(BaseModel):
    """
    Composite model for config/config.yaml.
    Every section of the YAML maps to one typed sub-model.
    """
    vault:            VaultConfig
    database:         DatabaseConfig      = Field(default_factory=DatabaseConfig)
    para_context_path: Path | None        = None
    providers:        ProvidersConfig     = Field(default_factory=ProvidersConfig)
    claude:           ClaudeConfig        = Field(default_factory=ClaudeConfig)
    ollama:           OllamaConfig        = Field(default_factory=OllamaConfig)
    mcp:              MCPConfig           = Field(default_factory=MCPConfig)
    self_learning:    SelfLearningConfig  = Field(default_factory=SelfLearningConfig)
    logging:          LoggingConfig       = Field(default_factory=LoggingConfig)
    env:              Env                 = "dev"

    @model_validator(mode="after")
    def validate_vault_root_exists(self) -> Self:
        """Fail fast: crash at startup if the vault path is wrong."""
        if not self.vault.root.exists():
            raise ValueError(
                f"Vault root does not exist: {self.vault.root}\n"
                f"Fix vault.root in config/config.yaml."
            )
        if not self.vault.root.is_dir():
            raise ValueError(
                f"Vault root is not a directory: {self.vault.root}"
            )
        return self

    @model_validator(mode="after")
    def validate_para_context_path(self) -> Self:
        """Warn (don't crash) if para_context_path is set but missing."""
        if self.para_context_path and not self.para_context_path.exists():
            logging.getLogger(__name__).warning(
                "para_context_path set but not found: %s — classify pipeline will skip PARA context.",
                self.para_context_path,
            )
        return self


# ===========================================================================
# 2. Sub-models for config/thresholds.yaml
# ===========================================================================

class ConfidenceBand(BaseModel):
    """
    Confidence thresholds that drive routing decisions.

    Three bands, evaluated top-down:

        score >= auto    → RouteDecision.AUTO
                           Pipeline acts immediately. Audit log records outcome.

        score >= suggest → RouteDecision.SUGGEST
                           AI has candidate destinations but confidence is
                           insufficient to act. Note stays in inbox, flagged
                           with AI's top candidates for human confirmation.

        score <  suggest → RouteDecision.CLUELESS
                           AI reviewed the note but could not form a useful
                           candidate. Note stays in inbox, flagged with a
                           "needs human — AI has no suggestion" signal.
                           Never silent. Always audit-logged.

    All thresholds live in config/thresholds.yaml — never in code.
    """
    auto:    float = Field(0.85, ge=0.0, le=1.0)
    suggest: float = Field(0.60, ge=0.0, le=1.0)   # renamed from `review`

    @model_validator(mode="after")
    def suggest_below_auto(self) -> Self:
        if self.suggest >= self.auto:
            raise ValueError(
                f"'suggest' ({self.suggest}) must be strictly less than 'auto' ({self.auto})."
            )
        return self

    def route(self, score: float) -> RouteDecision:
        """
        Map a confidence score to a RouteDecision.

        This is the single authoritative routing gate. All pipeline code
        calls this method — never compares against threshold floats directly.

        Usage:
            band = CONFIG.thresholds.for_pipeline("classify")
            decision = band.route(confidence_score)

            match decision:
                case RouteDecision.AUTO:
                    auto_move(note)
                case RouteDecision.SUGGEST:
                    flag_with_suggestions(note, candidates)
                case RouteDecision.CLUELESS:
                    flag_as_clueless(note)
        """
        if score >= self.auto:
            return RouteDecision.AUTO
        elif score >= self.suggest:
            return RouteDecision.SUGGEST
        else:
            return RouteDecision.CLUELESS


class Thresholds(BaseModel):
    """Schema for config/thresholds.yaml."""
    global_:   ConfidenceBand               = Field(default_factory=ConfidenceBand, alias="global")
    pipelines: dict[str, ConfidenceBand]    = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    def for_pipeline(self, pipeline_name: str) -> ConfidenceBand:
        """
        Return thresholds for a named pipeline, falling back to global.

        Usage:
            band = CONFIG.thresholds.for_pipeline("classify")
            if score >= band.auto:
                auto_move(note)
            elif score >= band.review:
                flag_for_review(note)
        """
        return self.pipelines.get(pipeline_name, self.global_)


# ===========================================================================
# 3. Sub-models for config/routing.yaml (Phase 2 placeholder)
# ===========================================================================

class PipelineRouting(BaseModel):
    """Per-pipeline LLM overrides. All fields optional — absent = use provider default."""
    provider:          Provider | None = None
    model:             str | None      = None
    fallback_provider: Provider | None = None


class Routing(BaseModel):
    """Schema for config/routing.yaml. Phase 2 fills this."""
    pipelines: dict[str, PipelineRouting] = Field(default_factory=dict)


# ===========================================================================
# 4. API keys — environment variables only, never YAML
# ===========================================================================

class ApiKeys(BaseSettings):
    """
    Reads secrets from environment variables or .env file.
    Never put real keys in config.yaml — this class is the only safe entrypoint.

        export ANTHROPIC_API_KEY="sk-ant-..."

    pydantic-settings raises a clear error at startup if validation fails,
    rather than crashing silently on the first API call.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key:    str | None = Field(default=None, alias="OPENAI_API_KEY")

    @field_validator("anthropic_api_key", "openai_api_key", mode="before")
    @classmethod
    def empty_string_to_none(cls, v: str | None) -> str | None:
        return v.strip() if v and v.strip() else None


# ===========================================================================
# 5. Composing model + loader
# ===========================================================================

class Config(BaseModel):
    """
    The one object every module imports.

    Quick reference:
        CONFIG.main.vault.inbox_path              → Path
        CONFIG.main.vault.projects_path           → Path
        CONFIG.main.providers.for_task("capture") → "claude" | "ollama"
        CONFIG.main.claude.model                  → str (haiku)
        CONFIG.main.claude.synthesis_model        → str (sonnet)
        CONFIG.main.ollama.embedding_model        → str
        CONFIG.main.self_learning.enabled         → bool
        CONFIG.main.mcp.port                      → int
        CONFIG.thresholds.for_pipeline("classify")→ ConfidenceBand
        CONFIG.routing.pipelines                  → dict (Phase 2)
        CONFIG.keys.anthropic_api_key             → str | None
    """
    main:       MainConfig
    thresholds: Thresholds
    routing:    Routing
    keys:       ApiKeys


def _load_yaml(filename: str) -> dict:
    """Read one YAML from config/. Returns {} if the file is empty."""
    path = _CONFIG_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Config file missing: {path}\n"
            f"Expected: config.yaml, thresholds.yaml, routing.yaml"
        )
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def load_config() -> "Config":
    """
    Load and validate all config files. Called once at module level.
 
    Raises:
        ConfigError — if any YAML file is missing OR any value fails the schema.
                      Wraps FileNotFoundError and Pydantic ValidationError so
                      callers get one typed exception to catch.
    """
    try:
        raw_main       = _load_yaml("config.yaml")
        raw_thresholds = _load_yaml("thresholds.yaml")
        raw_routing    = _load_yaml("routing.yaml")
 
        from pydantic import ValidationError  # local import avoids circular risk
 
        try:
            return Config(
                main=MainConfig(**raw_main),
                thresholds=Thresholds(**raw_thresholds),
                routing=Routing(**raw_routing),
                keys=ApiKeys(),
            )
        except ValidationError as exc:
            raise ConfigError(
                f"Config validation failed:\n{exc}"
            ) from exc
 
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file missing: {exc}") from exc


# ===========================================================================
# 6. Singleton
# ===========================================================================

# try:
#     CONFIG: Config = load_config()
#     logging.getLogger(__name__).info(
#         "Config loaded. env=%s  vault=%s",
#         CONFIG.main.env,
#         CONFIG.main.vault.root,
#     )
# except Exception as exc:
#     raise RuntimeError(
#         f"\n\n{'='*60}\n"
#         f"CONFIG LOAD FAILED — fix this before running anything.\n"
#         f"{'='*60}\n"
#         f"{exc}\n"
#     ) from exc


_CONFIG: Config | None = None


def __getattr__(name: str) -> object:
    if name == "CONFIG":
        global _CONFIG
        if _CONFIG is None:
            _CONFIG = load_config()
        return _CONFIG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

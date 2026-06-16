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
_REPO_ROOT = (
    _PROJECT_ROOT.parent
)  # one level above src/ — anchor for relative data paths
_CONFIG_DIR = _PROJECT_ROOT / "config"

# ---------------------------------------------------------------------------
# Type aliases — 3.12 `type` statement, runtime-enforced.
# ---------------------------------------------------------------------------
type Provider = Literal["claude", "claude_cli", "ollama", "openai"]
type Task = Literal[
    "classify",
    "synthesis",
    "documentation",
    "embeddings",
    "self_learn",
    "capture",
    "vision",
]
type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
type Env = Literal["dev", "prod", "test"]


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

    AUTO = "auto"
    SUGGEST = "suggest"
    CLUELESS = "clueless"


# ===========================================================================
# 1. Sub-models for config/config.yaml
# ===========================================================================


class VaultConfig(BaseModel):
    """
    Vault root + all sub-folder names.
    Add a new folder here + a matching @property for top-level paths.
    Parametrized sub-paths (per-project, per-domain) live in vault/paths.py.
    """

    root: Path
    inbox_dir: str = "inbox"
    projects_dir: str = "Projects"
    domain_dir: str = "Domain"
    documentation_dir: str = "Documentation"
    synthesis_dir: str = "Synthesis"
    briefings_dir: str = "Briefings"
    archive_dir: str = "Archive"
    attachment_dir: str = "attachment"
    summaries_subdir: str = ".summaries"
    no_edit_extensions: list[str] = Field(
        default_factory=lambda: [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]
    )

    @field_validator("no_edit_extensions", mode="before")
    @classmethod
    def _validate_no_edit_extensions(cls, v: object) -> list[str]:
        """Lowercase every entry and require a leading dot."""
        if v is None:
            return [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]
        if not isinstance(v, list):
            raise ValueError(
                f"no_edit_extensions must be a list, got {type(v).__name__}"
            )
        result: list[str] = []
        for ext in v:
            ext_str = str(ext).strip().lower()
            if not ext_str.startswith("."):
                raise ValueError(
                    f"no_edit_extensions entry '{ext}' is missing a leading dot. "
                    f"Each extension must start with '.' (e.g., '.pdf', not 'pdf')."
                )
            result.append(ext_str)
        return result

    # ── derived path helpers ──────────────────────────────────────────────
    # Always use these; never build paths by string concatenation elsewhere.
    @property
    def inbox_path(self) -> Path:
        return self.root / self.inbox_dir

    @property
    def projects_path(self) -> Path:
        return self.root / self.projects_dir

    @property
    def domain_path(self) -> Path:
        return self.root / self.domain_dir

    @property
    def documentation_path(self) -> Path:
        return self.root / self.documentation_dir

    @property
    def synthesis_path(self) -> Path:
        return self.root / self.synthesis_dir

    @property
    def briefings_path(self) -> Path:
        return self.root / self.briefings_dir

    @property
    def ai_output_dirs(self) -> tuple[str, ...]:
        return (self.briefings_dir, self.synthesis_dir, self.documentation_dir)

    @property
    def ai_output_paths(self) -> tuple[Path, ...]:
        return (self.briefings_path, self.synthesis_path, self.documentation_path)


class DatabaseConfig(BaseModel):
    # validate_assignment so the KMS_DB_PATH env override in load_config() is
    # resolved the same way as a YAML value.
    model_config = {"validate_assignment": True}

    path: Path = Path("./data/kb.db")

    @field_validator("path")
    @classmethod
    def _resolve_relative_to_repo_root(cls, v: Path) -> Path:
        """Anchor a relative db path at the repo root, not the process cwd.

        Without this, `kms` launched from any directory other than the repo
        root resolved `./data/kb.db` against the wrong cwd and sqlite failed
        with 'unable to open database file'.
        """
        return v if v.is_absolute() else (_REPO_ROOT / v).resolve()


class ProvidersConfig(BaseModel):
    """
    Per-task provider selection. Maps each pipeline task to "claude" or "ollama".
    Adding a new task = add a field here + add the task to the Task type alias.
    """

    classify: Provider = "claude"
    synthesis: Provider = "claude"
    embeddings: Provider = "ollama"
    self_learn: Provider = "claude"
    capture: Provider = "claude"
    vision: Provider = "openai"

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

    model: str = "claude-haiku-4-5-20251001"  # capture, classify, self_learn
    synthesis_model: str = "claude-sonnet-4-20250514"  # synthesis, documentation
    embedding_model: str = "voyage-3"  # via Voyage API (future)
    vision_model: str = ""  # empty = not configured
    max_tokens: int = 1024
    timeout: int = 60  # seconds


class OllamaConfig(BaseModel):
    """Ollama local server settings."""

    base_url: str = "http://localhost:11434"
    chat_model: str = "llama3"
    synthesis_model: str = "llama3"  # override with a larger model e.g. llama3
    embedding_model: str = "nomic-embed-text"
    vision_model: str = ""
    max_tokens: int = 1024
    timeout: int = 120
    delay_between_calls: int = 2  # seconds between batch calls


class OpenAICompatConfig(BaseModel):
    """Settings for any OpenAI-compatible endpoint (Fireworks, Together, etc.)."""

    base_url: str = "https://api.fireworks.ai/inference/v1"
    model: str = "accounts/fireworks/models/gpt-oss-20b"
    synthesis_model: str = "accounts/fireworks/models/llama-v3p1-70b-instruct"
    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    vision_model: str = ""  # empty = not configured; set to the vision model path
    max_tokens: int = 1024
    timeout: int = 60
    api_key_env: str = "FIREWORKS_API_KEY"  # name of the env var holding the key
    max_retries: int = Field(
        default=5, ge=0, le=20
    )  # openai SDK's built-in 429/5xx backoff


class ClaudeCliConfig(BaseModel):
    """Claude CLI subprocess provider settings."""

    cli_path: str = "claude"
    model: str = "claude-haiku-4-5-20251001"
    synthesis_model: str = "claude-sonnet-4-20250514"
    embedding_model: str = "voyage-3"  # interface parity; not used by CLI
    vision_model: str = ""  # empty = not configured
    max_tokens: int = 1024  # interface parity; CLI has no --max-tokens flag
    timeout: int = 60  # seconds passed to asyncio.wait_for


class ContextInjectionConfig(BaseModel):
    """Controls how the MCP server injects vault context into LLM conversations."""

    max_entities_per_dimension: int = Field(15, ge=1)
    max_orientation_facts_per_dimension: int = Field(5, ge=1)


class InspectConfig(BaseModel):
    """kms_inspect text-mode cap (Phase 9)."""

    max_text_refs: int = Field(5, ge=1)


class FactSearchConfig(BaseModel):
    """Fact search tuning (Phase 9)."""

    keyword_weight: float = Field(0.5, ge=0.0, le=1.0)
    max_results: int = Field(20, ge=1)


class MCPConfig(BaseModel):
    """MCP server settings (Roadmap 9)."""

    port: int = 3838
    host: str = "0.0.0.0"
    enable_http: bool = False
    context_injection: ContextInjectionConfig = Field(
        default_factory=ContextInjectionConfig
    )
    retrieval_score: "RetrievalScoreConfig" = Field(
        default_factory=lambda: RetrievalScoreConfig()
    )
    inspect: InspectConfig = Field(default_factory=InspectConfig)
    fact_search: FactSearchConfig = Field(default_factory=FactSearchConfig)


class RetrievalScoreConfig(BaseModel):
    """Retrieval score decay + sweep settings (Phase 9)."""

    decay_factor: float = Field(0.95, ge=0.0, le=1.0)
    sweep_interval_hours: int = Field(24, ge=1)


class SelfLearningConfig(BaseModel):
    """Controls how the self-learning pipeline adjusts prompts (Roadmap 8).

    Phase 10 additions: trust deltas, overwrite guard threshold,
    context injection filtering, and few-shot cap are all config-driven (C-06).
    """

    enabled: bool = True
    min_evaluations: int = 20
    confidence_threshold: float = Field(0.8, ge=0.0, le=1.0)
    include_examples_in_prompt: bool = True
    max_examples: int = 5
    # Phase 10 trust mechanics
    trust_confirm_delta: float = Field(0.05, ge=0.0, le=1.0)
    trust_reject_delta: float = Field(-0.10, ge=-1.0, le=0.0)
    trust_revise_base: float = Field(0.6, ge=0.0, le=1.0)
    overwrite_trust_threshold: float = Field(0.5, ge=0.0, le=1.0)
    min_trust_for_context: float = Field(0.3, ge=0.0, le=1.0)
    volatility_correction_count: int = Field(3, ge=1)
    max_corrections_per_prompt: int = Field(5, ge=0, le=20)


class LoggingConfig(BaseModel):
    level: LogLevel = "INFO"
    file: str = "logs/app.log"
    console: bool = True


class HandlersConfig(BaseModel):
    """Limits applied by handlers/* for filesystem and web extraction.

    All values are hard caps; exceeding them returns Failure(recoverable=False).
    Adjust in config/config.yaml under `handlers:`.
    """

    max_file_size_bytes: int = Field(50 * 1024 * 1024, ge=1)  # 50 MB
    max_web_fetch_bytes: int = Field(10 * 1024 * 1024, ge=1)  # 10 MB
    web_fetch_timeout_seconds: int = Field(30, ge=1)
    dns_resolve_timeout_seconds: int = Field(5, ge=1)
    max_redirects: int = Field(5, ge=0)


class RenameGateConfig(BaseModel):
    """Tunable parameters for the rename gate. Adjust in config/config.yaml under capture.rename_gate."""

    office_extensions: list[str] = Field(
        default_factory=lambda: [".md", ".docx", ".xlsx", ".pptx", ".txt"]
    )
    max_stem_length: int = Field(120, ge=10)
    # TODO: migrate _GENERIC_NAMES frozenset to generic_names: list[str] here (TD-GATE-1)


class VisionConfig(BaseModel):
    """Which file types get described and the size cap for vision AI calls."""

    describable_mime_prefixes: list[str] = Field(default_factory=lambda: ["image/"])
    max_vision_bytes: int = Field(10 * 1024 * 1024, ge=1)  # 10 MB default


class CaptureConfig(BaseModel):
    """Tunable parameters for the capture pipeline. Adjust in config/config.yaml."""

    cooldown_seconds: int = Field(60, ge=0)
    max_urls_per_note: int = Field(3, ge=0)
    rename_gate: RenameGateConfig = Field(default_factory=RenameGateConfig)  # type: ignore[arg-type]
    folder_cooldown_seconds: float = Field(5.0, ge=0.0)
    binary_settle_seconds: float = Field(5.0, ge=0.0)
    folder_max_workers: int = Field(4, ge=1)
    vision: VisionConfig = Field(default_factory=VisionConfig)


class SearchConfig(BaseModel):
    """Search/retrieval configuration. Read from config.yaml search: section."""

    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_dim: int = 1024
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    max_candidates: int = 20
    max_results: int = 10


class ClassifyConfig(BaseModel):
    """Classify pipeline configuration. Read from config.yaml classify: section.

    Tunables for Slice A — content-token cap and per-dimension fact cap.
    Adjust in config/config.yaml under `classify:`.
    """

    max_content_tokens: int = Field(10000, ge=1)
    max_entries_per_dimension: int = Field(50, ge=1)
    max_retries: int = Field(default=3, ge=1, le=20)


class TestingConfig(BaseModel):
    """Isolated vault used for manual testing-guide runs.

    Only consulted when `env: test` in config.yaml. When env is dev/prod this
    block is ignored and `vault.root` is used as-is.
    """

    vault_path: Path


class MainConfig(BaseModel):
    """
    Composite model for config/config.yaml.
    Every section of the YAML maps to one typed sub-model.
    """

    vault: VaultConfig
    testing: TestingConfig | None = None
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    para_context_path: Path | None = None
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    claude_cli: ClaudeCliConfig = Field(default_factory=ClaudeCliConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    openai_compat: OpenAICompatConfig = Field(default_factory=OpenAICompatConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    self_learning: SelfLearningConfig = Field(default_factory=SelfLearningConfig)  # type: ignore[arg-type]
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    handlers: HandlersConfig = Field(default_factory=HandlersConfig)  # type: ignore[arg-type]
    capture: CaptureConfig = Field(default_factory=CaptureConfig)  # type: ignore[arg-type]
    search: SearchConfig = Field(default_factory=SearchConfig)
    classify: ClassifyConfig = Field(default_factory=ClassifyConfig)
    env: Env = "dev"

    @model_validator(mode="after")
    def select_vault_by_env(self) -> Self:
        """When env=test, redirect vault.root to the isolated testing vault.

        Runs BEFORE validate_vault_root_exists (after-validators fire in
        definition order) so the existence check below validates the effective
        root. dev/prod leave vault.root untouched.
        """
        if self.env == "test":
            if self.testing is None:
                raise ValueError(
                    "env: test requires a `testing.vault_path` in config.yaml."
                )
            self.vault.root = self.testing.vault_path
        return self

    @model_validator(mode="after")
    def validate_vault_root_exists(self) -> Self:
        """Fail fast: crash at startup if the vault path is wrong."""
        if not self.vault.root.exists():
            raise ValueError(
                f"Vault root does not exist: {self.vault.root}\n"
                f"Fix vault.root in config/config.yaml."
            )
        if not self.vault.root.is_dir():
            raise ValueError(f"Vault root is not a directory: {self.vault.root}")
        return self

    @model_validator(mode="after")
    def validate_para_context_path(self) -> Self:
        """Validator placeholder — warning is emitted once in load_config() to avoid
        Pydantic v2 re-running this validator when MainConfig is nested inside Config."""
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

    auto: float = Field(0.85, ge=0.0, le=1.0)
    suggest: float = Field(0.60, ge=0.0, le=1.0)  # renamed from `review`

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

    global_: ConfidenceBand = Field(default_factory=ConfidenceBand, alias="global")  # type: ignore[arg-type]
    pipelines: dict[str, ConfidenceBand] = Field(default_factory=dict)

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

    provider: Provider | None = None
    model: str | None = None
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
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    kms_db_path: Path | None = Field(default=None, alias="KMS_DB_PATH")
    vault_root: Path | None = Field(default=None, alias="VAULT_ROOT")

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

    main: MainConfig
    thresholds: Thresholds
    routing: Routing
    keys: ApiKeys


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
        raw_main = _load_yaml("config.yaml")
        raw_thresholds = _load_yaml("thresholds.yaml")
        raw_routing = _load_yaml("routing.yaml")

        from pydantic import ValidationError  # local import avoids circular risk

        try:
            keys = ApiKeys()
            # COUPLING / TD-059: throwaway cloud bridge — removed at config split.
            # MUST be pre-construction: validate_vault_root_exists (config.py:372-382) runs at
            # MainConfig construction and VaultConfig has no validate_assignment.
            if keys.vault_root is not None:
                raw_main["vault"]["root"] = str(keys.vault_root)
            cfg = Config(
                main=MainConfig(**raw_main),
                thresholds=Thresholds(**raw_thresholds),
                routing=Routing(**raw_routing),
                keys=keys,
            )
            if keys.kms_db_path is not None:
                cfg.main.database.path = keys.kms_db_path
            # Warn once here — not in MainConfig.validate_para_context_path, because
            # Pydantic v2 re-runs model_validators when a nested model is passed to a
            # parent constructor, causing the warning to appear twice.
            if cfg.main.para_context_path and not cfg.main.para_context_path.exists():
                logging.getLogger(__name__).warning(
                    "para_context_path set but not found: %s — classify pipeline will skip PARA context.",
                    cfg.main.para_context_path,
                )
            return cfg
        except ValidationError as exc:
            raise ConfigError(f"Config validation failed:\n{exc}") from exc

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
CONFIG: Config  # mypy stub — runtime value provided by __getattr__


def __getattr__(name: str) -> object:
    if name == "CONFIG":
        global _CONFIG
        if _CONFIG is None:
            _CONFIG = load_config()
        return _CONFIG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""
tests/test_core/test_config.py

Full test coverage for core/config.py.

Test map (read this before adding new tests):
  Section 1 — _load_yaml()            file-reading primitives
  Section 2 — ConfidenceBand          threshold model validation
  Section 3 — Thresholds              for_pipeline() routing logic
  Section 4 — VaultConfig             path properties
  Section 5 — MainConfig              vault-root validator
  Section 6 — ApiKeys                 env-var reading + coercion
  Section 7 — Routing / LLMConfig     auxiliary model defaults
  Section 8 — load_config()           full-stack integration
  Section 9 — CONFIG singleton        smoke tests on the live object

⚠️  PREREQUISITE — one small change required in core/config.py
    load_config() must wrap FileNotFoundError and Pydantic ValidationError
    in ConfigError so callers get a single, typed exception. Add this block:

        from core.exceptions import ConfigError       # top of file

        def load_config() -> Config:
            try:
                raw_main      = _load_yaml("config.yaml")
                raw_thresholds = _load_yaml("thresholds.yaml")
                raw_routing   = _load_yaml("routing.yaml")
                return Config(
                    main=MainConfig(**raw_main),
                    thresholds=Thresholds(**raw_thresholds),
                    routing=Routing(**raw_routing),
                    keys=ApiKeys(),
                )
            except FileNotFoundError as exc:
                raise ConfigError(f"Config file missing: {exc}") from exc
            except ValidationError as exc:
                raise ConfigError(f"Config validation failed:\\n{exc}") from exc

    Without this change, Sections 8d-8f will raise the raw Pydantic /
    FileNotFoundError instead of ConfigError. All other sections are
    unaffected — they test models directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Import only the pieces we test — NOT the module-level CONFIG singleton.
# Importing CONFIG would trigger load_config() at collection time, which
# requires real config files to be present on every developer's machine.
# ---------------------------------------------------------------------------
from core.config import (
    ApiKeys,
    ClaudeConfig,
    ConfidenceBand,
    MainConfig,
    MCPConfig,
    OllamaConfig,
    PipelineRouting,
    ProvidersConfig,
    Routing,
    SelfLearningConfig,
    Thresholds,
    VaultConfig,
    RouteDecision,
)
from core.exceptions import ConfigError


# ===========================================================================
# Section 1 — _load_yaml() : file-reading primitives
# ===========================================================================

class TestLoadYaml:
    """_load_yaml reads from _CONFIG_DIR. We patch the directory to tmp_path
    so tests are hermetic and never touch the real config/ folder."""

    def test_reads_valid_yaml_file(self, tmp_path: Path, monkeypatch):
        """Happy path: well-formed YAML returns the expected dict."""
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        (tmp_path / "sample.yaml").write_text(
            "global:\n  auto: 0.85\n  suggest: 0.60\n"
        )
        result = cfg_module._load_yaml("sample.yaml")
        assert result == {"global": {"auto": 0.85, "suggest": 0.60}}

    def test_returns_empty_dict_for_empty_file(self, tmp_path: Path, monkeypatch):
        """
        yaml.safe_load on an empty file returns None.
        _load_yaml must coerce that to {} so callers can unpack safely.
        """
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        (tmp_path / "empty.yaml").write_text("")
        result = cfg_module._load_yaml("empty.yaml")
        assert result == {}

    def test_returns_empty_dict_for_comment_only_file(self, tmp_path: Path, monkeypatch):
        """A file with only YAML comments is functionally empty."""
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        (tmp_path / "comments.yaml").write_text("# just a comment\n# another\n")
        result = cfg_module._load_yaml("comments.yaml")
        assert result == {}

    def test_raises_file_not_found_for_missing_file(self, tmp_path: Path, monkeypatch):
        """
        A missing YAML is a configuration error, not a silent default.
        Must raise FileNotFoundError (load_config wraps this into ConfigError).
        """
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        with pytest.raises(FileNotFoundError, match="Config file missing"):
            cfg_module._load_yaml("does_not_exist.yaml")

    def test_error_message_names_the_missing_file(self, tmp_path: Path, monkeypatch):
        """The error message should tell the developer which file is absent."""
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        with pytest.raises(FileNotFoundError) as exc_info:
            cfg_module._load_yaml("thresholds.yaml")
        assert "thresholds.yaml" in str(exc_info.value)

    def test_raises_on_syntactically_invalid_yaml(self, tmp_path: Path, monkeypatch):
        """
        Invalid YAML syntax must surface as a yaml.YAMLError (not silently
        return a partial dict). load_config() will later wrap this into ConfigError.
        """
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        (tmp_path / "broken.yaml").write_text(
            "key: [unclosed bracket\n  sub: value\n"
        )
        with pytest.raises(yaml.YAMLError):
            cfg_module._load_yaml("broken.yaml")


# ===========================================================================
# Section 2 — ConfidenceBand : threshold model validation
# ===========================================================================

class TestConfidenceBand:
    """Pure model tests — no file I/O. Tests the Pydantic schema directly."""

    # ── Defaults ─────────────────────────────────────────────────────────────

    def test_default_auto_is_085(self):
        band = ConfidenceBand()
        assert band.auto == 0.85

    def test_default_suggest_is_060(self):
        band = ConfidenceBand()
        assert band.suggest == 0.60

    def test_defaults_are_floats(self):
        """
        Core contract: thresholds must be Python floats so callers can do
        `score >= band.auto` without implicit type coercion surprises.
        """
        band = ConfidenceBand()
        assert isinstance(band.auto, float)
        assert isinstance(band.suggest, float)

    # ── Parsing from YAML-sourced values ─────────────────────────────────────

    def test_integer_inputs_are_coerced_to_float(self):
        """
        YAML often gives you integers (auto: 1) instead of floats (auto: 1.0).
        Pydantic must coerce these — the field type is float, not int | float.
        """
        band = ConfidenceBand(auto=1, suggest=0)
        assert isinstance(band.auto, float)
        assert isinstance(band.suggest, float)

    def test_string_float_inputs_are_coerced(self):
        """Some YAML parsers return '0.85' as a string. Pydantic coerces it."""
        band = ConfidenceBand(auto="0.90", suggest="0.65")
        assert band.auto == pytest.approx(0.90)
        assert band.suggest == pytest.approx(0.65)

    def test_custom_values_parse_correctly(self):
        band = ConfidenceBand(auto=0.95, suggest=0.75)
        assert band.auto == pytest.approx(0.95)
        assert band.suggest == pytest.approx(0.75)

    # ── Boundary acceptance ───────────────────────────────────────────────────

    def test_accepts_auto_at_exactly_one(self):
        """Upper boundary — 1.0 is a valid 'always auto-execute' sentinel."""
        band = ConfidenceBand(auto=1.0, suggest=0.0)
        assert band.auto == 1.0

    def test_accepts_suggest_at_exactly_zero(self):
        """Lower boundary — 0.0 is valid (everything auto-suggested)."""
        band = ConfidenceBand(auto=1.0, suggest=0.0)
        assert band.suggest == 0.0

    # ── Constraint violations ─────────────────────────────────────────────────

    def test_rejects_suggest_greater_than_auto(self):
        """
        suggest >= auto is a logical contradiction (the suggest gate would be
        higher than the auto gate). Must be rejected at parse time.
        """
        with pytest.raises(ValidationError, match="suggest.*must be strictly less than"):
            ConfidenceBand(auto=0.70, suggest=0.80)

    def test_rejects_suggest_equal_to_auto(self):
        """Equal thresholds leaves no 'flag for suggest' band — also invalid."""
        with pytest.raises(ValidationError):
            ConfidenceBand(auto=0.75, suggest=0.75)

    def test_rejects_auto_above_one(self):
        """Probability > 1.0 is nonsensical."""
        with pytest.raises(ValidationError):
            ConfidenceBand(auto=1.01, suggest=0.60)

    def test_rejects_suggest_below_zero(self):
        """Probability < 0.0 is nonsensical."""
        with pytest.raises(ValidationError):
            ConfidenceBand(auto=0.85, suggest=-0.01)

    def test_rejects_auto_below_zero(self):
        with pytest.raises(ValidationError):
            ConfidenceBand(auto=-0.1, suggest=-0.5)


# ===========================================================================
# Section 3 — Thresholds : for_pipeline() routing logic
# ===========================================================================

class TestThresholds:

    def test_global_alias_parses_correctly(self):
        """
        'global' is a Python keyword, so the field is named global_ with an
        alias. Pydantic must accept {'global': {...}} from YAML correctly.
        """
        t = Thresholds(**{"global": {"auto": 0.90, "suggest": 0.65}})
        assert t.global_.auto == pytest.approx(0.90)

    def test_defaults_to_standard_global_band(self):
        """With no YAML input, the global band should be 0.85 / 0.60."""
        t = Thresholds()
        assert t.global_.auto == pytest.approx(0.85)
        assert t.global_.suggest == pytest.approx(0.60)

    def test_for_pipeline_returns_global_for_unknown_name(self):
        """
        An unknown pipeline name must fall back to the global band silently.
        This is the 'safe default' — never raise on an unrecognised pipeline.
        """
        t = Thresholds()
        band = t.for_pipeline("nonexistent_pipeline")
        assert band.auto == pytest.approx(0.85)
        assert band.suggest == pytest.approx(0.60)

    def test_for_pipeline_returns_override_when_configured(self):
        """A named pipeline with its own band must return that band, not global."""
        t = Thresholds(**{
            "global": {"auto": 0.85, "suggest": 0.60},
            "pipelines": {
                "classify": {"auto": 0.92, "suggest": 0.72},
            },
        })
        band = t.for_pipeline("classify")
        assert band.auto == pytest.approx(0.92)
        assert band.suggest == pytest.approx(0.72)

    def test_for_pipeline_other_pipelines_still_fall_back(self):
        """
        Only the named pipeline uses its override. Others must still use global.
        Regression guard: we once returned the wrong band here.
        """
        t = Thresholds(**{
            "global": {"auto": 0.85, "suggest": 0.60},
            "pipelines": {
                "classify": {"auto": 0.92, "suggest": 0.72},
            },
        })
        band = t.for_pipeline("capture")
        assert band.auto == pytest.approx(0.85)

    def test_for_pipeline_result_is_a_confidence_band(self):
        """for_pipeline() always returns a ConfidenceBand, never a raw dict."""
        t = Thresholds()
        band = t.for_pipeline("any_name")
        assert isinstance(band, ConfidenceBand)

    def test_empty_pipelines_dict_is_valid(self):
        """routing.yaml ships with `pipelines: {}` — that must parse without error."""
        t = Thresholds(**{"global": {"auto": 0.85, "suggest": 0.60}, "pipelines": {}})
        assert t.pipelines == {}


# ===========================================================================
# Section 4 — VaultConfig : path property helpers
# ===========================================================================

class TestVaultConfig:
    """
    Tests for all derived path properties on VaultConfig.
    These exist so callers never build paths by string concatenation elsewhere.
    VaultConfig has seven subdirectories: inbox, projects, domain,
    documentation, synthesis, briefings, archive.
    """

    @pytest.fixture()
    def vault(self, tmp_path: Path) -> VaultConfig:
        tmp_path.mkdir(exist_ok=True)
        return VaultConfig(root=tmp_path)

    # ── default path properties ───────────────────────────────────────────────

    def test_inbox_path_is_root_plus_inbox_dir(self, vault, tmp_path):
        assert vault.inbox_path == tmp_path / "inbox"

    def test_projects_path_is_root_plus_projects_dir(self, vault, tmp_path):
        assert vault.projects_path == tmp_path / "Projects"

    def test_domain_path_is_root_plus_domain_dir(self, vault, tmp_path):
        assert vault.domain_path == tmp_path / "Domain"

    def test_documentation_path_is_root_plus_documentation_dir(self, vault, tmp_path):
        assert vault.documentation_path == tmp_path / "Documentation"

    def test_synthesis_path_is_root_plus_synthesis_dir(self, vault, tmp_path):
        assert vault.synthesis_path == tmp_path / "Synthesis"

    def test_briefings_path_is_root_plus_briefings_dir(self, vault, tmp_path):
        assert vault.briefings_path == tmp_path / "Briefings"

    def test_summaries_subdir_default_is_dotted_summaries(self, vault, tmp_path):
        """summaries_subdir defaults to '.summaries' (the hidden sibling folder name)."""
        assert vault.summaries_subdir == ".summaries"

    def test_summaries_subdir_override_is_respected(self, tmp_path):
        """summaries_subdir can be overridden in config."""
        vc = VaultConfig(root=tmp_path, summaries_subdir=".alt-summaries")
        assert vc.summaries_subdir == ".alt-summaries"

    def test_attachment_path_property_removed(self, vault):
        """attachment_path property is removed; per-project paths live in vault/paths.py."""
        assert not hasattr(vault, "attachment_path")

    # ── custom dir names are honoured ─────────────────────────────────────────

    def test_custom_inbox_dir_reflected_in_property(self, tmp_path):
        """Overriding a dir name in YAML must propagate to the path property."""
        vc = VaultConfig(root=tmp_path, inbox_dir="Drop")
        assert vc.inbox_path == tmp_path / "Drop"

    def test_custom_projects_dir_reflected_in_property(self, tmp_path):
        vc = VaultConfig(root=tmp_path, projects_dir="Work")
        assert vc.projects_path == tmp_path / "Work"

    # ── type coercion ─────────────────────────────────────────────────────────

    def test_root_is_coerced_from_string_to_path(self, tmp_path):
        """YAML delivers root as a string. Pydantic must coerce it to Path."""
        vc = VaultConfig(root=str(tmp_path))
        assert isinstance(vc.root, Path)

    # ── no_edit_extensions Field ──────────────────────────────────────────────

    def test_no_edit_extensions_default_has_six_dot_prefixed_lowercase(self, tmp_path):
        """Default extension list contains exactly the six expected strings, all
        dot-prefixed and lowercase."""
        vc = VaultConfig(root=tmp_path)
        assert vc.no_edit_extensions == [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]
        assert len(vc.no_edit_extensions) == 6
        assert all(ext.startswith(".") for ext in vc.no_edit_extensions)
        assert all(ext == ext.lower() for ext in vc.no_edit_extensions)

    def test_no_edit_extensions_custom_value_roundtrips(self, tmp_path):
        """A custom list of extensions is stored and returned as-is after validation."""
        vc = VaultConfig(root=tmp_path, no_edit_extensions=[".pdf", ".dwg", ".psd"])
        assert vc.no_edit_extensions == [".pdf", ".dwg", ".psd"]

    def test_no_edit_extensions_validator_lowercases(self, tmp_path):
        """Validator lowercases uppercase entries: .PDF → .pdf."""
        vc = VaultConfig(root=tmp_path, no_edit_extensions=[".PDF", ".PNG", ".JpG"])
        assert vc.no_edit_extensions == [".pdf", ".png", ".jpg"]

    def test_no_edit_extensions_validator_rejects_missing_dot(self, tmp_path):
        """A value without a leading dot ('pdf') raises ValidationError with a
        message naming the offending value."""
        with pytest.raises(ValidationError, match="no_edit_extensions"):
            VaultConfig(root=tmp_path, no_edit_extensions=["pdf"])

    def test_no_edit_extensions_absent_kwarg_uses_default(self, tmp_path):
        """Constructing without no_edit_extensions gives the Python default list."""
        vc = VaultConfig(root=tmp_path)  # no no_edit_extensions kwarg
        assert vc.no_edit_extensions == [".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp"]

    # ── ai_output_dirs / ai_output_paths properties ───────────────────────────

    def test_ai_output_dirs_returns_default_triple(self, tmp_path):
        """ai_output_dirs returns the three AI-output folder names with defaults."""
        vc = VaultConfig(root=tmp_path)
        assert vc.ai_output_dirs == ("Briefings", "Synthesis", "Documentation")

    def test_ai_output_dirs_reflects_overridden_dir(self, tmp_path):
        """ai_output_dirs reflects an overridden *_dir Field immediately."""
        vc = VaultConfig(root=tmp_path, briefings_dir="Reports")
        assert vc.ai_output_dirs == ("Reports", "Synthesis", "Documentation")

    def test_ai_output_paths_returns_resolved_paths(self, tmp_path):
        """ai_output_paths returns resolved Path objects for the three AI-output dirs."""
        vc = VaultConfig(root=tmp_path)
        assert vc.ai_output_paths == (
            tmp_path / "Briefings",
            tmp_path / "Synthesis",
            tmp_path / "Documentation",
        )


# ===========================================================================
# Section 5 — MainConfig : vault-root model_validator
# ===========================================================================

class TestMainConfig:
    """
    MainConfig calls .exists() and .is_dir() at parse time (model_validator).
    These tests verify the fail-fast behaviour that prevents silent mis-configs.
    """

    def test_accepts_existing_directory(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert cfg.vault.root == vault_dir

    def test_rejects_nonexistent_vault_root(self, tmp_path: Path):
        """
        A typo in config.yaml's vault.root should crash startup immediately,
        not produce silent failures six call-frames later.
        """
        ghost = tmp_path / "does_not_exist"
        with pytest.raises(ValidationError, match="does not exist"):
            MainConfig(vault={"root": str(ghost)})

    def test_rejects_file_as_vault_root(self, tmp_path: Path):
        """vault.root must be a directory, not a file."""
        not_a_dir = tmp_path / "i_am_a_file.txt"
        not_a_dir.write_text("oops")
        with pytest.raises(ValidationError, match="not a directory"):
            MainConfig(vault={"root": str(not_a_dir)})

    def test_default_env_is_dev(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert cfg.env == "dev"

    def test_accepts_prod_env(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)}, env="prod")
        assert cfg.env == "prod"

    def test_rejects_unknown_env(self, vault_dir: Path):
        with pytest.raises(ValidationError):
            MainConfig(vault={"root": str(vault_dir)}, env="staging")

    def test_logging_defaults_are_applied(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert cfg.logging.level == "INFO"
        assert cfg.logging.console is True

    def test_default_provider_for_classify_is_claude(self, vault_dir: Path):
        """ProvidersConfig replaces the old LLMConfig — check its defaults."""
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert cfg.providers.classify == "claude"

    def test_default_provider_for_embeddings_is_ollama(self, vault_dir: Path):
        """Embeddings are always routed to the local Ollama model by default."""
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert cfg.providers.embeddings == "ollama"

    def test_claude_default_model_contains_haiku(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert "haiku" in cfg.claude.model.lower()

    def test_self_learning_enabled_by_default(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert cfg.self_learning.enabled is True

    def test_mcp_default_port_is_3838(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)})
        assert cfg.mcp.port == 3838


# ===========================================================================
# Section 6 — ApiKeys : env-var reading and coercion
# ===========================================================================

class TestApiKeys:
    """
    ApiKeys reads from environment variables only — never YAML.
    Tests use monkeypatch so they never bleed real key values.
    """

    def test_reads_anthropic_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake-123")
        # Force a fresh read (not cached)
        keys = ApiKeys()
        assert keys.anthropic_api_key == "sk-ant-fake-123"

    def test_reads_openai_key_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-fake-456")
        keys = ApiKeys()
        assert keys.openai_api_key == "sk-openai-fake-456"

    def test_missing_key_defaults_to_none(self, monkeypatch):
        """
        Keys are optional — callers must do a None-check before using them.
        Not-having a key must NOT blow up at startup.
        """
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        keys = ApiKeys()
        assert keys.anthropic_api_key is None
        assert keys.openai_api_key is None

    def test_empty_string_becomes_none(self, monkeypatch):
        """
        CI pipelines often export ANTHROPIC_API_KEY="" rather than unsetting.
        An empty string must be treated the same as absent — see empty_string_to_none.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        keys = ApiKeys()
        assert keys.anthropic_api_key is None

    def test_whitespace_only_becomes_none(self, monkeypatch):
        """'   ' is functionally empty — coerce to None."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "   ")
        keys = ApiKeys()
        # Our validator checks `if v` — whitespace-only is falsy
        assert keys.anthropic_api_key is None


# ===========================================================================
# Section 7 — Routing / LLMConfig : auxiliary model defaults
# ===========================================================================

# ===========================================================================
# Section 7 — Auxiliary config models: ProvidersConfig, ClaudeConfig,
#              OllamaConfig, MCPConfig, SelfLearningConfig
# ===========================================================================


# ===========================================================================
# Section 7b — CaptureConfig
# ===========================================================================

class TestCaptureConfig:
    """CaptureConfig defaults and validation."""

    def test_default_cooldown_seconds_is_60(self):
        from core.config import CaptureConfig
        c = CaptureConfig()
        assert c.cooldown_seconds == 60

    def test_default_max_urls_per_note_is_3(self):
        from core.config import CaptureConfig
        c = CaptureConfig()
        assert c.max_urls_per_note == 3

    def test_cooldown_seconds_rejects_negative(self):
        from core.config import CaptureConfig
        with pytest.raises(ValidationError):
            CaptureConfig(cooldown_seconds=-1)

    def test_max_urls_per_note_rejects_negative(self):
        from core.config import CaptureConfig
        with pytest.raises(ValidationError):
            CaptureConfig(max_urls_per_note=-1)

    def test_default_binary_settle_seconds_is_5(self):
        from core.config import CaptureConfig
        c = CaptureConfig()
        assert c.binary_settle_seconds == 5.0

    def test_binary_settle_seconds_rejects_negative(self):
        from core.config import CaptureConfig
        with pytest.raises(ValidationError):
            CaptureConfig(binary_settle_seconds=-1.0)

    def test_main_config_has_capture_field(self, vault_dir: Path):
        cfg = MainConfig(vault={"root": str(vault_dir)})
        from core.config import CaptureConfig
        assert isinstance(cfg.capture, CaptureConfig)
        assert cfg.capture.cooldown_seconds == 60
        assert cfg.capture.max_urls_per_note == 3

class TestRouting:

    def test_empty_pipelines_dict_is_the_default(self):
        r = Routing()
        assert r.pipelines == {}

    def test_pipelines_dict_is_empty_from_routing_yaml_placeholder(self):
        """routing.yaml currently ships with `pipelines: {}`. Verify it parses."""
        r = Routing(**{"pipelines": {}})
        assert r.pipelines == {}

    def test_pipeline_routing_all_fields_optional(self):
        """PipelineRouting has no required fields — Phase 2 will fill them."""
        pr = PipelineRouting()
        assert pr.provider is None
        assert pr.model is None
        assert pr.fallback_provider is None


class TestProvidersConfig:

    def test_default_classify_provider_is_claude(self):
        p = ProvidersConfig()
        assert p.classify == "claude"

    def test_default_embeddings_provider_is_ollama(self):
        """Embeddings run locally on Ollama by default — never pay per-call."""
        p = ProvidersConfig()
        assert p.embeddings == "ollama"

    def test_for_task_returns_correct_provider(self):
        """for_task() must return the per-task provider, not a fixed value."""
        p = ProvidersConfig(classify="claude", embeddings="ollama")
        assert p.for_task("classify") == "claude"
        assert p.for_task("embeddings") == "ollama"

    def test_rejects_unknown_provider_value(self):
        """Only 'claude', 'claude_cli', 'ollama', 'openai' are valid providers."""
        with pytest.raises(ValidationError):
            ProvidersConfig(classify="unknown_provider")

    def test_accepts_claude_cli_as_provider(self):
        """'claude_cli' is a valid provider — subprocess mode, no API key needed."""
        p = ProvidersConfig(capture="claude_cli")
        assert p.capture == "claude_cli"


class TestClaudeConfig:

    def test_default_model_is_haiku(self):
        """Haiku is the fast/cheap default for most tasks."""
        c = ClaudeConfig()
        assert "haiku" in c.model.lower()

    def test_synthesis_model_is_sonnet(self):
        """Sonnet is the smarter model reserved for synthesis tasks."""
        c = ClaudeConfig()
        assert "sonnet" in c.synthesis_model.lower()

    def test_default_timeout_is_60(self):
        c = ClaudeConfig()
        assert c.timeout == 60

    def test_default_max_tokens_is_1024(self):
        c = ClaudeConfig()
        assert c.max_tokens == 1024

    def test_custom_model_overrides_default(self):
        c = ClaudeConfig(model="claude-opus-4")
        assert c.model == "claude-opus-4"


class TestOllamaConfig:

    def test_default_base_url(self):
        o = OllamaConfig()
        assert o.base_url == "http://localhost:11434"

    def test_default_embedding_model(self):
        o = OllamaConfig()
        assert o.embedding_model == "nomic-embed-text"

    def test_default_timeout_is_120(self):
        """Ollama runs locally and can be slow — generous default timeout."""
        o = OllamaConfig()
        assert o.timeout == 120

    def test_delay_between_calls_default(self):
        o = OllamaConfig()
        assert o.delay_between_calls == 2


class TestMCPConfig:

    def test_default_port_is_3838(self):
        m = MCPConfig()
        assert m.port == 3838

    def test_default_host_listens_on_all_interfaces(self):
        m = MCPConfig()
        assert m.host == "0.0.0.0"

    def test_http_disabled_by_default(self):
        """HTTP transport is off by default — enable explicitly for VPS deploys."""
        m = MCPConfig()
        assert m.enable_http is False


class TestSelfLearningConfig:

    def test_enabled_by_default(self):
        s = SelfLearningConfig()
        assert s.enabled is True

    def test_confidence_threshold_is_float(self):
        s = SelfLearningConfig()
        assert isinstance(s.confidence_threshold, float)

    def test_confidence_threshold_default_is_080(self):
        s = SelfLearningConfig()
        assert s.confidence_threshold == pytest.approx(0.8)

    def test_rejects_confidence_threshold_above_one(self):
        with pytest.raises(ValidationError):
            SelfLearningConfig(confidence_threshold=1.1)

    def test_rejects_confidence_threshold_below_zero(self):
        with pytest.raises(ValidationError):
            SelfLearningConfig(confidence_threshold=-0.1)

    def test_min_evaluations_default(self):
        s = SelfLearningConfig()
        assert s.min_evaluations == 20

    def test_max_examples_default(self):
        s = SelfLearningConfig()
        assert s.max_examples == 5


class TestClaudeCliConfig:

    def test_default_cli_path_is_claude(self):
        from core.config import ClaudeCliConfig
        c = ClaudeCliConfig()
        assert c.cli_path == "claude"

    def test_default_model_is_haiku(self):
        from core.config import ClaudeCliConfig
        c = ClaudeCliConfig()
        assert "haiku" in c.model.lower()

    def test_synthesis_model_is_sonnet(self):
        from core.config import ClaudeCliConfig
        c = ClaudeCliConfig()
        assert "sonnet" in c.synthesis_model.lower()

    def test_default_timeout_is_60(self):
        from core.config import ClaudeCliConfig
        c = ClaudeCliConfig()
        assert c.timeout == 60

    def test_default_max_tokens_is_1024(self):
        from core.config import ClaudeCliConfig
        c = ClaudeCliConfig()
        assert c.max_tokens == 1024

    def test_embedding_model_is_voyage(self):
        from core.config import ClaudeCliConfig
        c = ClaudeCliConfig()
        assert "voyage" in c.embedding_model.lower()

    def test_custom_cli_path(self):
        from core.config import ClaudeCliConfig
        c = ClaudeCliConfig(cli_path="/usr/local/bin/claude")
        assert c.cli_path == "/usr/local/bin/claude"


# ===========================================================================
# Section 8 — load_config() : full-stack integration
# ===========================================================================

class TestLoadConfig:
    """
    These tests call load_config() directly — bypassing the module-level
    CONFIG singleton — by monkeypatching _CONFIG_DIR to a temp directory.
    """

    # ── 8a  Happy-path loading ───────────────────────────────────────────────

    def test_returns_config_object(self, monkeypatch, config_dir):
        """load_config() with valid files must return a Config, not raise."""
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)

        from core.config import Config
        result = cfg_module.load_config()
        assert isinstance(result, Config)

    def test_loads_all_three_yamls(self, monkeypatch, config_dir):
        """
        Verify that data from each of the three files actually ends up in
        the returned Config. This catches a bug where only one file is read.
        """
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)

        cfg = cfg_module.load_config()

        # From config.yaml
        assert cfg.main.vault.root.is_dir()
        assert cfg.main.env == "dev"

        # From thresholds.yaml
        assert isinstance(cfg.thresholds.global_.auto, float)
        assert isinstance(cfg.thresholds.global_.suggest, float)

        # From routing.yaml
        assert isinstance(cfg.routing.pipelines, dict)

    def test_threshold_values_are_floats(self, loaded_config):
        """
        Main requirement: after parsing, confidence thresholds must be floats.
        Downstream code does arithmetic on these; ints would silently work
        until a comparison like `score >= band.auto` with score=0.87 fails.
        """
        band = loaded_config.thresholds.global_
        assert isinstance(band.auto, float)
        assert isinstance(band.suggest, float)

    def test_pipeline_thresholds_are_floats(self, loaded_config):
        """Per-pipeline overrides must also be floats."""
        band = loaded_config.thresholds.for_pipeline("classify")
        assert isinstance(band.auto, float)
        assert isinstance(band.suggest, float)

    def test_for_pipeline_classify_uses_override(self, loaded_config):
        """The fixture sets classify.auto=0.90 — verify it wins over global 0.85."""
        band = loaded_config.thresholds.for_pipeline("classify")
        assert band.auto == pytest.approx(0.90)

    def test_for_pipeline_unknown_falls_back_to_global(self, loaded_config):
        band = loaded_config.thresholds.for_pipeline("does_not_exist")
        assert band.auto == pytest.approx(0.85)

    def test_vault_root_is_a_path_object(self, loaded_config):
        assert isinstance(loaded_config.main.vault.root, Path)

    # ── 8b  ConfigError on missing YAML files ────────────────────────────────

    def test_raises_config_error_when_config_yaml_missing(
        self, monkeypatch, tmp_path, vault_dir
    ):
        """
        If config.yaml is absent, load_config() must raise ConfigError — NOT
        a bare FileNotFoundError — so callers get a single typed exception.

        Requires load_config() to wrap FileNotFoundError in ConfigError.
        See module docstring for the required code change.
        """
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)
        # tmp_path has none of the three files

        with pytest.raises(ConfigError, match="Config file"):
            cfg_module.load_config()

    def test_raises_config_error_when_thresholds_yaml_missing(
        self, monkeypatch, tmp_path, vault_dir
    ):
        """Only config.yaml present — thresholds.yaml missing."""
        import yaml as _yaml
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        (tmp_path / "config.yaml").write_text(
            _yaml.dump({"vault": {"root": str(vault_dir)}})
        )
        # thresholds.yaml and routing.yaml are absent

        with pytest.raises(ConfigError):
            cfg_module.load_config()

    def test_raises_config_error_when_routing_yaml_missing(
        self, monkeypatch, tmp_path, vault_dir
    ):
        """config.yaml and thresholds.yaml present — routing.yaml missing."""
        import yaml as _yaml
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", tmp_path)

        (tmp_path / "config.yaml").write_text(
            _yaml.dump({"vault": {"root": str(vault_dir)}})
        )
        (tmp_path / "thresholds.yaml").write_text(
            _yaml.dump({"global": {"auto": 0.85, "suggest": 0.60}})
        )

        with pytest.raises(ConfigError):
            cfg_module.load_config()

    # ── 8c  ConfigError on schema violations ─────────────────────────────────

    def test_raises_config_error_for_invalid_threshold_values(
        self, monkeypatch, config_dir
    ):
        """
        suggest >= auto is a schema violation in ConfidenceBand.
        Must surface as ConfigError, not a raw Pydantic ValidationError.
        """
        import yaml as _yaml
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)

        # Overwrite thresholds.yaml with an invalid band
        bad_thresholds = config_dir / "thresholds.yaml"
        bad_thresholds.write_text(
            _yaml.dump({
                "global": {
                    "auto": 0.60,    # ← suggest (0.80) > auto (0.60): invalid
                    "suggest": 0.80,
                },
                "pipelines": {},
            })
        )
        with pytest.raises(ConfigError, match="validation failed"):
            cfg_module.load_config()

    def test_raises_config_error_for_invalid_provider(
        self, monkeypatch, config_dir
    ):
        """An unrecognised LLM provider in config.yaml must raise ConfigError."""
        import yaml as _yaml
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)

        # Read, mutate, rewrite config.yaml
        with (config_dir / "config.yaml").open() as f:
            data = _yaml.safe_load(f)
        data["providers"] = {"classify": "gemini"}     # not in Provider literal
        (config_dir / "config.yaml").write_text(_yaml.dump(data))

        with pytest.raises(ConfigError):
            cfg_module.load_config()

    def test_raises_config_error_for_nonexistent_vault(
        self, monkeypatch, config_dir
    ):
        """Vault root that doesn't exist must raise ConfigError, not ValueError."""
        import yaml as _yaml
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)

        with (config_dir / "config.yaml").open() as f:
            data = _yaml.safe_load(f)
        data["vault"]["root"] = "/this/path/does/not/exist/ever"
        (config_dir / "config.yaml").write_text(_yaml.dump(data))

        with pytest.raises(ConfigError):
            cfg_module.load_config()

    def test_config_error_is_subclass_of_kms_error(self):
        """
        ConfigError must inherit from KMSError so callers can catch all
        project errors with a single `except KMSError` guard.
        """
        from core.exceptions import KMSError
        assert issubclass(ConfigError, KMSError)

    # ── 8d  keys passthrough ─────────────────────────────────────────────────

    def test_api_key_is_accessible_on_loaded_config(
        self, monkeypatch, config_dir
    ):
        """After load_config(), the API key from env must be on .keys."""
        import core.config as cfg_module
        monkeypatch.setattr(cfg_module, "_CONFIG_DIR", config_dir)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-99")

        cfg = cfg_module.load_config()
        assert cfg.keys.anthropic_api_key == "sk-ant-test-99"


# ===========================================================================
# Section 9 — CONFIG singleton : smoke tests
# ===========================================================================
#
# These import the live CONFIG object. They run successfully only when the
# real config/ files exist and are valid. They serve as a canary: if any
# of these fail on a fresh clone, the config files need attention.
#
# Mark them with @pytest.mark.smoke so they can be excluded in CI:
#     pytest -m "not smoke"
# ---------------------------------------------------------------------------

@pytest.mark.smoke
class TestConfigSingleton:

    @pytest.fixture(autouse=True)
    def _import_singleton(self):
        """
        Import is deferred to here so collection-time failures don't kill
        the other test sections when running without real config files.
        """
        try:
            from core.config import CONFIG as _cfg
            self.cfg = _cfg
        except (RuntimeError, Exception) as exc:
            pytest.skip(f"CONFIG singleton not loadable: {exc}")

    def test_singleton_is_importable(self):
        assert self.cfg is not None

    def test_global_auto_threshold_is_float(self):
        assert isinstance(self.cfg.thresholds.global_.auto, float)

    def test_global_suggest_threshold_is_float(self):
        assert isinstance(self.cfg.thresholds.global_.suggest, float)

    def test_suggest_is_below_auto(self):
        band = self.cfg.thresholds.global_
        assert band.suggest < band.auto

    def test_vault_root_exists(self):
        assert self.cfg.main.vault.root.exists()

    def test_vault_root_is_directory(self):
        assert self.cfg.main.vault.root.is_dir()

    def test_env_is_dev_or_prod(self):
        assert self.cfg.main.env in ("dev", "prod")

    def test_default_provider_for_classify_is_valid(self):
        assert self.cfg.main.providers.classify in ("claude", "ollama")

    def test_routing_pipelines_is_dict(self):
        assert isinstance(self.cfg.routing.pipelines, dict)
    
    def test_route_returns_auto_at_threshold(self):
        band = ConfidenceBand(auto=0.85, suggest=0.60)
        assert band.route(0.85) == RouteDecision.AUTO
        assert band.route(1.00) == RouteDecision.AUTO

    def test_route_returns_suggest_between_thresholds(self):
        band = ConfidenceBand(auto=0.85, suggest=0.60)
        assert band.route(0.60) == RouteDecision.SUGGEST
        assert band.route(0.72) == RouteDecision.SUGGEST
        assert band.route(0.849) == RouteDecision.SUGGEST

    def test_route_returns_clueless_below_suggest(self):
        band = ConfidenceBand(auto=0.85, suggest=0.60)
        assert band.route(0.59) == RouteDecision.CLUELESS
        assert band.route(0.00) == RouteDecision.CLUELESS

    def test_route_clueless_is_never_silent(self):
        """
        Regression guard: score < suggest must return CLUELESS, not None or
        a silent pass. The whole point is that nothing goes unacknowledged.
        """
        band = ConfidenceBand(auto=0.85, suggest=0.60)
        result = band.route(0.10)
        assert result is not None
        assert result == RouteDecision.CLUELESS
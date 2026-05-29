# PROMPTS dict and Jinja2 Environment are module-level singletons; StrictUndefined always

`PROMPTS: dict[str, Prompt]` loaded eagerly at `llm/prompt_loader.py` import time. `_JINJA_ENV = Environment(undefined=StrictUndefined)` is a module-level singleton. `Prompt` has no `model` or `temperature` fields — those are deferred until `LLMProvider.complete()` accepts them.

**Status:** accepted (post-review fix #4 2026-05-14)

**Considered Options**

- Lazy loading — rejected: chose eager for fail-fast at startup (OQ-L3).
- Per-call `Environment()` construction — rejected: expensive on hot pipeline paths.
- `DebugUndefined` or default `Undefined` — rejected: silent rendering of missing template vars sends garbled strings to the LLM.

**Consequences**

- Missing or malformed prompt files fail at startup, not mid-pipeline.
- All AI prompts are YAML files in `prompts/` — never inline f-strings (hook-enforced).
- Phase 1 pipelines call `PROMPTS["name"].render(**vars)` to get `(system_str, user_str)`.
- Per-prompt model/temperature overrides require extending `LLMProvider.complete()` first.

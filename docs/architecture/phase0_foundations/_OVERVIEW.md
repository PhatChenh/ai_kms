<!-- ARCH-STORY:PHASE0 -->
# Component Diagram — Phase 0: Foundations
Scope: Every module in the foundation layer and how they relate. All other phases
import from here; this phase imports from nothing above it.

Status: ✅ All components complete.
Box standard: ~20 char wide, ~7 row high. Full descriptions in Diagram Notes.

---

## Component Map

```
 ┌───────────────────────────────────────────────────────────────────────────────────┐
 │  Phase 0 — Foundations                                                            │
 │                                                                                   │
 │  ┌──────────────────────┐   ┌──────────────────────┐                             │
 │  │ config.yaml          │   │ Config Singleton      │                             │
 │  │ thresholds.yaml      │──►│ core/config.py        │◄── imported by all modules  │
 │  │ routing.yaml         │   │ ✅ [extensible:config]│                             │
 │  │ tags.yaml            │   │                      │                             │
 │  └──────────────────────┘   └──────────┬───────────┘                             │
 │                                        │ CONFIG singleton                         │
 │  ─────────────── Core Primitives ──────▼───────────────────────────────────────  │
 │                                                                                   │
 │  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐   │
 │  │ Result Type          │  │ Pipeline Runner      │  │ Audit Facade         │   │
 │  │ core/result.py       │  │ core/pipeline.py     │  │ core/audit.py        │   │
 │  │ ✅ [closed]          │  │ ✅ [closed]          │  │ ✅ [closed]          │   │
 │  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘   │
 │                                                                                   │
 │  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐   │
 │  │ Confidence Router    │  │ Tag Validator        │  │ Rename Gate          │   │
 │  │ core/confidence.py   │  │ core/tags.py         │  │ core/rename_gate.py  │   │
 │  │ ✅ [ext: config]     │  │ ✅ [ext: config]     │  │ ✅ [ext: config]     │   │
 │  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘   │
 │                                                                                   │
 │  ┌──────────────────────┐  ┌──────────────────────┐                             │
 │  │ Logging Setup        │  │ Exceptions           │                             │
 │  │ core/logging_setup   │  │ core/exceptions.py   │                             │
 │  │ ✅ [closed]          │  │ ✅ [closed]          │                             │
 │  └──────────────────────┘  └──────────────────────┘                             │
 │                                                                                   │
 │  ─────────────── LLM Layer ─────────────────────────────────────────────────    │
 │                                                                                   │
 │  ┌──────────────────────┐  ┌──────────────────────┐  prompts/*.yaml             │
 │  │ Provider Factory     │  │ Prompt Loader        │  ┌──────────────────────┐   │
 │  │ llm/provider.py      │  │ llm/prompt_loader    │  │ summarize.yaml       │   │
 │  │ ✅ [ext: protocol]   │──│ ✅ [ext: config]     │◄─│ extract_metadata.yaml│   │
 │  └──────────┬───────────┘  └──────────────────────┘  │ summarize_attach.yaml│   │
 │             │ routes to one of:                       └──────────────────────┘   │
 │  ┌──────────▼────────────────────────────────────┐                              │
 │  │ Claude  │ ClaudeCLI  │ Ollama  │ OpenAI-compat │                             │
 │  └──────────────────────────────────────────────-─┘                              │
 │                                                                                   │
 │  ─────────────── Storage Layer ─────────────────────────────────────────────    │
 │                                                                                   │
 │  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐   │
 │  │ DB Manager           │  │ Audit Log            │  │ Document Index       │   │
 │  │ storage/db.py        │  │ storage/audit_log    │  │ storage/documents    │   │
 │  │ ✅ [ext: migrations] │  │ ✅ [closed]          │  │ ✅ [closed]          │   │
 │  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘   │
 │                                                                                   │
 │  ─────────────── Vault Layer ───────────────────────────────────────────────    │
 │                                                                                   │
 │  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐   │
 │  │ Path Helpers         │  │ Frontmatter Parser   │  │ Note Reader          │   │
 │  │ vault/paths.py       │  │ vault/frontmatter    │  │ vault/reader.py      │   │
 │  │ ✅ [closed]          │  │ ✅ [closed]          │  │ ✅ [closed]          │   │
 │  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘   │
 │                                                                                   │
 │  ┌──────────────────────┐                                                        │
 │  │ Note Writer          │  ← ONLY code allowed to write to vault                │
 │  │ vault/writer.py      │    checks updated_by_human before every write         │
 │  │ ✅ [closed]          │                                                        │
 │  └──────────────────────┘                                                        │
 └───────────────────────────────────────────────────────────────────────────────────┘
              │                              │
              ▼                              ▼
   ┌──────────────────────┐     ┌──────────────────────┐
   │ SQLite Database      │     │ Obsidian Vault        │
   │ (stays local)        │     │ (files on disk)       │
   └──────────────────────┘     └──────────────────────┘
```

---

## Diagram Notes

| Module | What it does |
|---|---|
| **config.yaml + .yaml files** | Human-editable YAML. Controls: which AI provider, vault root path, confidence thresholds, routing rules, tag taxonomy. Change behavior here — never in Python. |
| **Config Singleton** | Reads and validates all YAML at startup. Fails loudly if anything is missing. Every module imports `CONFIG` — never re-reads YAML. Warning: importing CONFIG at module scope fails if vault path does not exist on that machine (breaks tests). |
| **Result Type** | Every public function returns `Success(value)` or `Failure(error)`. No raw returns. No None. Callers must handle both branches. |
| **Pipeline Runner** | Runs a list of async stages in sequence. If any stage fails, stops and returns the failure. Never swallows exceptions silently. Catches `Exception` (not `BaseException`) so Ctrl-C still works. |
| **Audit Facade** | Pipelines call `audit.write(decision)` to record every AI action. Translates an `AIDecision` into a storage row and calls the DB. Callers never touch `storage/audit_log` directly. |
| **Confidence Router** | Takes an `AIDecision` with a confidence score, returns: AUTO (auto-files it) / SUGGEST (asks you to confirm) / CLUELESS (flags for your decision). Thresholds are in YAML config — never hardcoded in pipeline code. |
| **Tag Validator** | Checks AI-generated tags against the allowed taxonomy in `config/tags.yaml`. Violations logged as `TAG_VIOLATION` audit entries — never silently accepted or dropped. |
| **Rename Gate** | Given an existing filename and an AI-suggested title, decides: SKIP (keep name) / AUGMENT (add topic suffix) / FULL_RENAME (replace). Rules from config. No AI calls. |
| **Logging Setup** | Configures structlog. Creates a correlation ID per pipeline run that flows through all log lines and audit entries for that run, allowing one run to be traced end-to-end. |
| **Provider Factory** | `get_provider(task, config)` returns the right AI provider for the task. Synthesis tasks get a smarter (and costlier) model. All providers implement the same interface. |
| **Prompt Loader** | Loads all `prompts/*.yaml` eagerly at startup. Pipelines call `PROMPTS["name"].render(**vars)` to get a ready-to-send prompt. No prompt text ever lives in Python code. |
| **DB Manager** | Applies versioned `.sql` migration files at startup in order. New database columns = new `.sql` file. No in-code `ALTER TABLE`. |
| **Audit Log** | Append-only SQL table. Stores: timestamp, pipeline, stage, decision, confidence score, reasoning, source file paths, correlation ID. Phase 8 (Briefing) reads from this exclusively. |
| **Document Index** | Index of every note in the vault. Integer primary key stays stable even when notes are renamed or moved. Phase 3 (Search) and Phase 8 (Briefing) read from this. |
| **Path Helpers** | Computes where every file type should live. `project_attachment(name)`, `domain_attachment(name)`, `project_summaries(name)`, `domain_archive(name)`. No hardcoded paths anywhere else. |
| **Frontmatter Parser** | Reads and writes the YAML header block of every markdown note. `NoteMetadata` model holds: title, summary, tags, type, source_file, attachment_path, status, updated_by_human. |
| **Note Reader** | Reads a note from disk. Returns `Success(note)` or `Failure(error)`. Never raises exceptions. |
| **Note Writer** | The **only** code allowed to write files to the vault. Checks `updated_by_human` before every AI write — if set, skips the write entirely. Idempotent: safe to call twice. |

**Extension posture legend:**
- `[ext: config]` — change behavior by editing a YAML file; no code changes needed
- `[ext: protocol]` — new AI provider = new class that implements the interface; nothing else changes
- `[ext: migrations]` — new DB columns = new numbered `.sql` file; nothing else changes
- `[closed]` — final by design; one version only

---

## Key Flows

### CONFIG bootstrap on startup

```
  cli/main.py starts
          │
          │  load_config() — called exactly once
          ▼
  Reads:
    config/config.yaml       ← vault path, provider settings
    config/thresholds.yaml   ← confidence gate cutoffs
    config/routing.yaml      ← which pipeline uses which AI model
    ANTHROPIC_API_KEY        ← from environment variable
          │
          │  produces one validated CONFIG object
          ▼
  CONFIG singleton available at:
    from core.config import CONFIG

  ⚠ CONFIG validates vault path at import time.
    Any test or module that imports CONFIG at module level
    will fail on machines without the vault.
    → Import CONFIG inside functions, not at top of file.
```

### Provider selection per task

```
  Pipeline calls: get_provider(task="capture", config=CONFIG.main)
          │
          ▼
  Is task in SYNTHESIS_TASKS (synthesis / documentation)?
    YES → use synthesis_model (smarter, higher cost)
    NO  → use standard model
          │
          ▼
  Returns one of:
    ClaudeProvider       (Anthropic API via HTTPS)
    ClaudeCliProvider    (local subprocess, no API key needed)
    OllamaProvider       (local Ollama server)
    OpenAICompatProvider (any OpenAI-compatible endpoint)

  All implement: await provider.complete(system, user) → Result[LLMResponse]
```

### Audit write flow

```
  Pipeline stage makes an AI decision
          │
          ▼
  core/audit.write(
    decision = AIDecision(action, confidence, reasoning, source_ids),
    pipeline = "capture",
    stage    = "metadata",
    ...
  )
          │
          ▼
  core/audit translates → AuditEntry (frozen dataclass)
          │
          ▼
  storage/audit_log.append(entry)
          │
          ▼
  SQL INSERT into audit_log table
  correlation_id filled from contextvars if not explicitly passed

  ⚠ Never call storage/audit_log directly from a pipeline.
    Always go through core/audit.write().
```

<!-- /ARCH-STORY:PHASE0 -->

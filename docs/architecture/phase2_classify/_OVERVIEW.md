<!-- ARCH-STORY:PHASE2 -->
# Component Diagram — Phase 2: Classify + Route

Scope: Every module built or planned for Phase 2. Covers how captured notes
in the inbox get classified by AI, confidence-gated, routed to the right
project or domain folder, and logged. Also covers the Project Registry
(already built) and Batch-ID association (already built).

Status: 🔄 In progress. Pre-requisites complete (Project Registry + Batch-ID Fix,
1004 tests). Core classify pipeline: planned.

Box standard: ~20 char wide, ~7 row high. Full descriptions in Diagram Notes.

---

## Component Map

```
 ┌──────────────────────────────────────────────────────────────────────────────────┐
 │  Phase 2 — Classify + Route                                                      │
 │                                                                                  │
 │  ┌──────────────────────┐                                                        │
 │  │ CLI Entry Points     │  kms classify <file>   kms classify --scan            │
 │  │ cli/main.py          │                                                        │
 │  │ ⬜ planned            │                                                        │
 │  └──────────┬───────────┘                                                        │
 │             │ calls                                                              │
 │             ▼                                                                    │
 │  ┌──────────────────────────────────────────────────────────────────────────┐    │
 │  │  Classify Pipeline       pipelines/classify.py         ⬜ planned        │    │
 │  │                                                                          │    │
 │  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌──────────┐  │    │
 │  │  │ 1. Classify │───►│ 2. Confid.  │───►│ 3. Route    │───►│ 4. Move  │  │    │
 │  │  │   (AI)      │    │    Gate     │    │   (paths)   │    │  (write) │  │    │
 │  │  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘    └────┬─────┘  │    │
 │  │         │                  │                  │                 │         │    │
 │  │         │                  │                  │                 │         │    │
 │  │         ▼                  ▼                  ▼                 ▼         │    │
 │  │  ┌─────────────────────────────────────────────────────────────────────┐ │    │
 │  │  │ 5. Decision Log         core/audit.py          (every outcome)     │ │    │
 │  │  └───────────────────────────────────────────────────────────────────────┘ │    │
 │  └──────────────────────────────────────────────────────────────────────────┘    │
 │             │                         │                         │                 │
 │             │ reads from              │ thresholds from         │ uses            │
 │             ▼                         ▼                         ▼                 │
 │  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐    │
 │  │ Project Registry     │  │ Confidence Config    │  │ Classification       │    │
 │  │ vault/registry.py    │  │ config/thresholds    │  │ Prompt               │    │
 │  │ ✅ complete           │  │ .yaml                │  │ prompts/classify     │    │
 │  │                      │  │ ⬜ planned            │  │ .yaml                │    │
 │  │ build_registry()     │  │                      │  │ ⬜ planned            │    │
 │  │ LiveRegistry         │  │ auto / suggest /     │  │                      │    │
 │  │ format_for_prompt()  │  │ human thresholds     │  │ destination +        │    │
 │  │ watcher hookup       │  │                      │  │ confidence +         │    │
 │  └──────────────────────┘  └──────────────────────┘  │ reasoning            │    │
 │                                                       └──────────────────────┘    │
 │  ┌──────────────────────────────────────────────────────────────────────────┐    │
 │  │  Batch-ID Association (pre-req, ✅ complete)                              │    │
 │  │                                                                          │    │
 │  │  vault/paths.py::is_batch_subfolder()                                    │    │
 │  │  storage/batches.py::find_by_folder_path()                               │    │
 │  │  pipelines/capture.py batch-stamp pre-step                               │    │
 │  │  vault/watcher.py Sub-step g2 (binary re-home stamps batch_id)           │    │
 │  └──────────────────────────────────────────────────────────────────────────┘    │
 │                                                                                  │
 └──────────────────────────────────────────────────────────────────────────────────┘
             │                              │                      │
             ▼                              ▼                      ▼
 ┌──────────────────────┐      ┌──────────────────────┐  ┌──────────────────────┐
 │ Obsidian Vault       │      │ SQLite Database      │  │ Anthropic Claude     │
 │ (files moved to      │      │ (audit log +         │  │ (AI classification   │
 │  project/domain)     │      │  batch_id + status)  │  │  decisions)          │
 └──────────────────────┘      └──────────────────────┘  └──────────────────────┘
```

---

## Data Flow — Single Note Classification

```
  Note in inbox (has summary + tags from Phase 1)
         │
         ▼
  ┌─────────────────────┐      ┌──────────────────────┐
  │ 1. CLASSIFY (AI)    │─────►│ Project Registry     │
  │    Reads note +     │◄─────│ (valid destinations) │
  │    asks AI where    │      └──────────────────────┘
  │    it belongs       │
  └────────┬────────────┘
           │ destination + confidence + reasoning
           ▼
  ┌─────────────────────┐      ┌──────────────────────┐
  │ 2. CONFIDENCE GATE  │◄─────│ config/thresholds    │
  │    Routes based on  │      │ (numbers live here,  │
  │    confidence score │      │  never in code)      │
  └────────┬────────────┘      └──────────────────────┘
           │
     ┌─────┼──────────────┐
     │     │              │
    HIGH  MEDIUM          LOW
     │     │              │
     ▼     ▼              ▼
  ┌──────┐ ┌──────────┐ ┌──────────────┐
  │Route │ │Flag note │ │Leave in inbox│
  │+Move │ │wait for  │ │mark "needs   │
  │      │ │user      │ │review"       │
  └──┬───┘ └────┬─────┘ └──────┬───────┘
     │          │               │
     └──────────┼───────────────┘
                │
                ▼
  ┌─────────────────────┐
  │ 5. DECISION LOG     │
  │    Every outcome    │
  │    is audited       │
  └─────────────────────┘
```

---

## Connections to Phase 1 Infrastructure

```
  Phase 2 component              Uses from Phase 0/1
  ──────────────────────         ─────────────────────────────────────
  Classify (AI step)         ──► llm/provider.py (AI call abstraction)
                             ──► llm/prompt_loader.py (loads classify.yaml)
                             ──► vault/reader.py (reads note content)
                             ──► vault/registry.py (destination list)

  Confidence Gate            ──► core/config.py (loads thresholds from YAML)

  Route                      ──► vault/paths.py (resolve_placement, helpers)

  Move                       ──► vault/writer.py (move_note, upsert)
                             ──► vault/move_guard.py (suppress watcher re-home)

  Decision Log               ──► core/audit.py (write audit entry)
                             ──► storage/audit_log.py (underlying storage)

  Project Registry (built)   ──► vault/paths.py (load_valid_domains)
                             ──► vault/reader.py (read CLAUDE.md tags)
                             ──► vault/watcher.py (live mutation hookup)

  Batch-ID Fix (built)       ──► vault/paths.py (is_batch_subfolder)
                             ──► storage/batches.py (find_by_folder_path)
                             ──► storage/documents.py (update_batch_id)
```

---

## Diagram Notes

| Module | What it does |
|---|---|
| **CLI Entry Points** | `kms classify <file>` runs classify pipeline on one note. `kms classify --scan` runs it on every unclassified note in inbox. Thin wrapper — no logic. |
| **Classify (AI step)** | Loads `prompts/classify.yaml`, fills placeholders (note summary, tags, valid destinations from registry), calls Claude via `llm/provider.py`. Returns: destination name, confidence 0–1, one-sentence reasoning. Must return `Result` — never raises. |
| **Confidence Gate** | Reads thresholds from `config/thresholds.yaml`. Maps confidence score to action: auto-move, flag-for-review, or mark-clueless. No numbers in code — all from config. |
| **Route** | Translates destination name → full vault path using `vault/paths.py` helpers. Verifies folder exists. Returns `Failure` if destination missing. |
| **Move** | Calls `vault/writer.py::move_note()`. Respects `updated_by_human` gate — refuses to move human-edited notes. Uses `move_guard` to prevent watcher from re-homing the file back. |
| **Decision Log** | Writes audit entry for every classification: note path, destination, confidence, reasoning, action taken. Runs even for notes left in inbox. Non-negotiable — Phase 8 Briefing reads from this. |
| **Project Registry** | ✅ Built. In-memory map of projects → domains. Scanned at startup, updated live via watcher events. `format_for_prompt()` serializes the map for AI context. |
| **Batch-ID Association** | ✅ Built. `is_batch_subfolder()` identifies batch-worthy folders. `find_by_folder_path()` deduplicates batches. Capture pipeline stamps `batch_id` before running. Watcher stamps `batch_id` on binary re-home. |
| **Classification Prompt** | YAML file at `prompts/classify.yaml`. Template with placeholders for note content, tags, and registry output. Instructs AI to return structured response. Never hardcoded in Python. |
| **Confidence Config** | Thresholds in `config/thresholds.yaml`. Controls the gate routing. Changing a threshold = editing one YAML value, no code changes. |

---

## What's Built vs Coming

```
  BUILT (pre-reqs)             COMING (core classify)
  ────────────────             ─────────────────────────
  ✅ Project Registry           → Classification Prompt (YAML)
  ✅ Batch-ID Association       → Classify pipeline (AI step)
  ✅ TD-042 deprecated strip    → Confidence Gate
                               → Route + Move
                               → Decision Log
                               → CLI: kms classify
```

---

## Extension Points

| Component | Extension type | How to extend |
|---|---|---|
| Project Registry | `[extensible: config]` | New domain = create folder, registry auto-discovers |
| Confidence Gate | `[extensible: config]` | Change thresholds in YAML — no code change |
| Classify prompt | `[extensible: config]` | Edit YAML template — no code change |
| Decision Log | `[closed]` | All audit writes go through `core/audit.py` |
| Route | `[extensible: protocol]` | New destination types = new path helper in `vault/paths.py` |

---

## Success Criteria (from roadmap)

After Phase 2 is complete:
1. Drop note in inbox → AI classifies and files it (or flags for review)
2. Run `kms classify --scan` → every unclassified note processed at once
3. Notes needing human review stay in inbox with visible flag
4. Every AI decision (even "don't know") is logged for briefing consumption
5. CLUELESS binary markers from Phase 1 get resolved (classify and route both files)

<!-- /ARCH-STORY:PHASE2 -->

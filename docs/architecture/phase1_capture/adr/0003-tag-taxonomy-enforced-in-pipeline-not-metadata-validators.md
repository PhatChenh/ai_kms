# Tag taxonomy enforced in pipeline, not in NoteMetadata validators

Tag validation (`validate_tags` in `core/tags.py`) runs in the pipeline's `metadata` stage, not in `NoteMetadata` Pydantic field validators. `NoteMetadata.type` and `NoteMetadata.domain` are convenience fields derived from `type/<name>` and `domain/<name>` tags.

**Status:** accepted (2026-05-20)

**Considered Options**

- Pydantic `field_validator` on `NoteMetadata.tags` — rejected: `NoteMetadata` reads existing notes with old vocabulary; strict validator breaks backward compat.
- Remove `type`/`domain` convenience fields entirely — rejected: user chose to keep both for Obsidian property queries.

**Consequences**

- All pipelines that generate tags (capture, classify, synthesis) must call `validate_tags` from `core/tags.py`.
- Tag violations are logged as `TAG_VIOLATION` audit entries (separate from `CAPTURED`).
- `NoteMetadata.type` is always derived from the `type/<name>` tag by stripping prefix.

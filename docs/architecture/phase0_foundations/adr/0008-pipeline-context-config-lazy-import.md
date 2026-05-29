# PipelineContext.config uses TYPE_CHECKING guard; CONFIG loaded lazily inside run_pipeline

`from core.config import Config` is gated under `if TYPE_CHECKING`. The `CONFIG` singleton is imported inside the `run_pipeline` body only when `context=None`. Tests pass `config=MagicMock()` via explicit `PipelineContext`.

**Status:** accepted

**Consequences**

- `CONFIG` validates vault root at import time. Importing it at module scope in `core/pipeline.py` would make the pipeline unimportable on machines without the vault.
- This lazy-import pattern applies to any module imported by tests that doesn't require real vault config at import time.

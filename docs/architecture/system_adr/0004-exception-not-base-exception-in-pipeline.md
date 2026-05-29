# run_pipeline catches Exception, not BaseException

`run_pipeline` uses `except Exception as exc` — stage bugs are caught and returned as `Failure`; `SystemExit`, `KeyboardInterrupt`, and `GeneratorExit` propagate normally.

**Status:** accepted (amended from original plan during code review 2026-05-14)

**Considered Options**

- `BaseException` — original plan; rejected because it swallows signals and makes Ctrl-C unresponsive.
- `PipelineError` only — too narrow; stages can throw any exception type.

**Consequences**

- `run_pipeline` guarantees callers always receive a `Result`, never a raw exception.
- The `except Exception` catch is a safety net for stage *bugs*, not a substitute for proper error handling. Stages must return `Failure` for expected errors.

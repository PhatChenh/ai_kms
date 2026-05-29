# Pydantic v2 model_validator(mode="after") side-effects must live in load_config(), not nested validators

Logging side-effects that should fire exactly once at config load must be placed in `load_config()` after the fully-built `Config` object is available — not inside `MainConfig` or other nested model validators.

**Status:** accepted (bug fix 2026-05-22)

**Considered Options**

- `model_config = ConfigDict(revalidate_instances='never')` on outer model — tested; does not suppress re-validation of nested validators in Pydantic v2.
- Class-level `_warned` flag on `MainConfig` — invasive, breaks immutability expectations.

**Consequences**

- Pydantic v2 re-runs `after` validators on a nested model instance whenever that instance is passed to the parent model's constructor. A validator in `MainConfig` fires during both `MainConfig(**raw)` and `Config(main=main_instance, ...)` — producing duplicate log output.
- Any new `model_validator(mode="after")` on nested config models must NOT produce logging side-effects. All startup warnings belong in `load_config()`.

# Discussion Log: LLM Layer

---

## Phase 2 — `llm/prompt_loader.py`
_Completed: 2026-05-14_

### Exchanges

**Q:** What is `StrictUndefined` and what effect does it have?

**A:** Jinja2 has three undefined-variable modes. The default (`Undefined`) silently renders a missing variable as an empty string. `DebugUndefined` renders it as the literal `{{ varname }}`. `StrictUndefined` raises `UndefinedError` immediately when a missing variable is accessed. We use `StrictUndefined` so a misconfigured prompt call fails loudly instead of sending a garbled string to the LLM.

_Key concept: Jinja2 undefined variable modes_

---

**Q:** What does `Environment(undefined=StrictUndefined)` do, and what is `Environment`?

```python
env = Environment(undefined=StrictUndefined)
```

**A:** `Environment` is Jinja2's engine configuration object. It holds rendering settings — undefined behavior, delimiters, extensions. `from_string()` on it compiles a template string using those settings. Think of it as: configure the engine once, then feed it template strings to compile and render.

_Key concept: Jinja2 Environment as engine config_

---

**Q:** What does `env.from_string(self.system).render(**vars)` do step by step?

```python
env.from_string(self.system).render(**vars),
env.from_string(self.user).render(**vars),
```

**A:** Three steps chained: (1) `from_string(self.system)` compiles the string into a `Template` object — parses `{{ }}` tokens, builds an AST. (2) `.render(**vars)` substitutes variables and returns the final string. (3) The same happens to `self.user` on the next line. A system string with no `{{ }}` passes through unchanged; a user string with `{{ message }}` gets the variable substituted.

_Key concept: Jinja2 template compile-then-render pattern_

---

**Q:** Does `render()` output go to the audit log, or does it get sent to the AI?

**A:** The `render()` output is what gets sent to the AI. `render()` produces `(system_str, user_str)`, and those two strings are passed directly into `provider.complete(system_str, user_str)`, which sends them as the chat messages to Claude/OpenAI/Ollama. The audit log records the AI's decision afterward — it does not receive the raw prompt strings.

_Key concept: render() output is the LLM input, not audit input_

---

**Q:** What does `Prompt.model_validate(data)` do, and is it from Pydantic?

```python
data = yaml.safe_load(path.read_text(encoding="utf-8"))
prompt = Prompt.model_validate(data)
```

**A:** Yes, it is a Pydantic v2 class method. `yaml.safe_load()` returns a plain Python dict. `model_validate(data)` takes that dict, validates the types, fills in defaults for optional fields, and raises `ValidationError` if required fields are missing — then returns a typed `Prompt` instance. It is equivalent to `Prompt(**data)` but accepts a dict instead of keyword arguments. The two steps are: parse YAML → validate into typed model.

_Key concept: Pydantic model_validate for dict-to-model conversion_

---

## Phase 3 — Revise `llm/provider.py` + config
_Completed: 2026-05-14_

### Exchanges

**Q:** Why did three tests in `test_logging.py` error with "fixture not found" when the functions just take string arguments?

**A:** `test_logging.py` was written as a standalone manual runner script, not a pytest module. Its test functions take plain string arguments meant to be passed by the `main()` function at the bottom. When pytest collects the file (because it starts with `test_`), it sees those string parameters and tries to resolve them as pytest fixtures — which don't exist. The fix is to add conftest fixtures that set up the logging context (call `setup_logging()`, generate a correlation id, write log lines) and return the id — making the functions work as real pytest tests.

```python
# Manual runner pattern (original) — args supplied by main(), not pytest
def test_required_fields_present(correlation_id: str) -> tuple[bool, str]:
    ...

# main() supplies the args manually:
run_test("...", lambda: test_required_fields_present(run_a_id))
```

_Key concept: pytest fixture injection vs manual argument passing_

---

**Q:** What does `PytestReturnNotNoneWarning` mean?

**A:** Pytest expects test functions to return `None`. A test passes or fails by raising `AssertionError` or not — the return value is ignored. The warning fires when a test function returns something other than `None` (like `tuple[bool, str]`). It suggests you may have written `return result` when you meant `assert result`. The tests still run, but the return value is silently discarded — if the function returns `(False, "error message")`, pytest still marks it PASSED because no exception was raised. Fix: replace `return False, "msg"` with `assert False, "msg"` and `return True, ""` with no return.

```python
# Before — pytest ignores the return; always "passes"
def test_log_file_created() -> tuple[bool, str]:
    exists = Path("logs/kms.log").exists()
    return exists, "logs/kms.log not found"

# After — actually fails when condition is false
def test_log_file_created() -> None:
    assert Path("logs/kms.log").exists(), "logs/kms.log not found"
```

_Key concept: pytest pass/fail via AssertionError, not return value_

---

## Phase 4 — `llm/openai_provider.py`
_Completed: 2026-05-14_

### Exchanges

**Q:** Why did `monkeypatch.delenv("FIREWORKS_API_KEY")` not prevent `OpenAIProvider.__init__` from finding the key?

```python
def test_raises_config_error_when_env_var_missing(self, monkeypatch):
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    ...
    OpenAIProvider(config)  # did NOT raise — key still found
```

**A:** `monkeypatch.delenv` removes the key from `os.environ` for the duration of the test. But `OpenAIProvider.__init__` calls `load_dotenv(_ENV_FILE, override=False)` before reading `os.environ`. `override=False` means "don't overwrite keys already in environ" — but after `monkeypatch` cleared the key, the key is no longer in environ, so `load_dotenv` loads it fresh from `.env` anyway. Net result: monkeypatch removes the key, then `load_dotenv` puts it back. Fix: patch `load_dotenv` to a no-op in unit tests that need to simulate a missing key.

```python
with patch("llm.openai_provider.load_dotenv"), pytest.raises(ConfigError, match="FIREWORKS_API_KEY"):
    OpenAIProvider(config)
```

_Key concept: load_dotenv(override=False) still loads missing keys — monkeypatch.delenv is not enough_

---

**Q:** Why was the `.env` file in YAML format (`FIREWORKS_API_KEY: 'value'`) instead of dotenv format (`FIREWORKS_API_KEY=value`), and does it matter?

**A:** Dotenv files use shell assignment syntax (`KEY=value`), not YAML. `python-dotenv`'s `load_dotenv()` parses the `KEY=value` format. A YAML-formatted `.env` (`KEY: 'value'`) is not valid dotenv syntax — `load_dotenv` either ignores the line or misparses the key name (including the colon). The fix is to write the file in correct dotenv format. The distinction matters because the two formats look similar but are parsed by completely different rules.

_Key concept: dotenv format is KEY=value, not YAML KEY: value_

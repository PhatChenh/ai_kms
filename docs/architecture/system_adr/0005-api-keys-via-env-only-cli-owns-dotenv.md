# API keys resolved via os.environ.get() only; cli/main.py owns load_dotenv

Providers call `os.environ.get(key_name)` and raise `ConfigError` if absent. `load_dotenv` is called exactly once in `cli/main.py` before any imports. `tests/test_llm/conftest.py` loads `.env` once for the test session.

**Status:** accepted (post-review fix 2026-05-14)

**Considered Options**

- `load_dotenv` inside each provider `__init__` with a hardcoded path — rejected: breaks when provider code is installed as a wheel (path resolves to site-packages, not the repo root).

**Consequences**

- Any new provider must NOT call `load_dotenv`. New CLI subcommands inherit the bootstrap via `cli/main.py`. Test files that need API keys must either use `monkeypatch.setenv` or rely on `tests/test_llm/conftest.py`.

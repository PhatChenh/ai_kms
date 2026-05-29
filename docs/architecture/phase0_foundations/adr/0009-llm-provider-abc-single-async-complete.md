# LLMProvider ABC: single async complete(system, user) -> Result[LLMResponse] method

All providers implement one abstract async method. `LLMResponse` is a frozen dataclass (`content: str`, `model: str`, `usage: dict`). Factory is `get_provider(task, config) -> LLMProvider` in `llm/provider.py`.

**Status:** accepted

**Considered Options**

- `embed()` on the ABC — rejected: embeddings are `sentence-transformers` responsibility.
- Sync interface — rejected: blocks event loop, violates async pipeline decision.
- `json_mode` parameter — rejected: JSON formatting belongs in prompt YAML `system` field.

**Consequences**

- Pipelines must `await provider.complete(system, user)`.
- `usage` must be populated via `resp.usage.model_dump()` — never store SDK usage objects directly (not JSON-serializable).
- Per-prompt `model` / `temperature` overrides are not supported by the current signature; extend it when needed.

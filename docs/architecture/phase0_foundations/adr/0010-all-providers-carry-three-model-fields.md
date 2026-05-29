# All three providers carry model, synthesis_model, embedding_model fields; SYNTHESIS_TASKS is a shared constant

`ClaudeConfig`, `OllamaConfig`, and `OpenAICompatConfig` all have three model fields. `get_provider(task, config)` passes `task` to all providers. Each selects `synthesis_model` if `task in SYNTHESIS_TASKS`, else `model`. `SYNTHESIS_TASKS = frozenset({"synthesis", "documentation"})` lives in `llm/provider.py`.

**Status:** accepted (post-plan extension 2026-05-14)

**Considered Options**

- Only Claude having per-task model selection — original plan; rejected when full-Ollama mode proved it also needs a smarter model for synthesis.

**Consequences**

- New pipeline tasks added to the `Task` type alias must be evaluated for inclusion in `SYNTHESIS_TASKS`.
- New providers must accept `task: Task` and apply the same selection logic.
- `_embedding_model` is stored but not yet routed — Phase 3 retrieval will wire it.

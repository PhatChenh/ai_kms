"""
llm/claude_provider.py

Claude implementation of AIProvider.

Key design: the task determines which model is used.
- synthesis / documentation → claude.synthesis_model (Sonnet — smarter, slower)
- everything else           → claude.model           (Haiku — fast, cheap)

This means pipelines don't need to think about model selection. They just call
get_provider("synthesis", CONFIG.main) and automatically get the right model.
"""

import json
import os

from anthropic import Anthropic

from core.config import ClaudeConfig, Task
from llm.provider import AIProvider

# Tasks that need the smarter synthesis model instead of the fast default.
_SYNTHESIS_TASKS: frozenset[Task] = frozenset({"synthesis", "documentation"})


class ClaudeProvider(AIProvider):
    """Calls the Claude API via the official Anthropic Python SDK."""

    def __init__(self, config: ClaudeConfig, task: Task = "capture") -> None:
        """
        Args:
            config: The validated ClaudeConfig from MainConfig.
            task:   The pipeline task this provider is being created for.
                    Used to select model (haiku vs sonnet).
        """
        # Key resolution order: env var → config field (which should be a placeholder).
        # The validator in ApiKeys already strips empty strings, so we only need env here.
        api_key = os.environ.get("ANTHROPIC_API_KEY") or config.__dict__.get("api_key")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY not set. "
                "Export it in your shell or add it to .env — never put it in config.yaml."
            )

        self.client     = Anthropic(api_key=api_key)
        self.max_tokens = config.max_tokens
        self.timeout    = config.timeout

        # Select model based on the task.
        self.model = (
            config.synthesis_model if task in _SYNTHESIS_TASKS
            else config.model
        )

    # ── public interface ──────────────────────────────────────────────────

    def chat(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
        """Send a prompt to Claude and return the text response."""
        if json_mode:
            suffix = "\n\nReturn ONLY valid JSON. No markdown, no explanation."
            system_prompt = (system_prompt + suffix) if system_prompt else suffix.strip()

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text

    def embed(self, text: str) -> list[float]:
        """Claude has no embedding endpoint — route embeddings to Ollama."""
        raise NotImplementedError(
            "Claude does not support embeddings. "
            "Set providers.embeddings = 'ollama' in config/config.yaml."
        )
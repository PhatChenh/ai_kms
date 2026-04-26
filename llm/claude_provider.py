# llm/claude_provider.py

import os
import json
from anthropic import Anthropic
from llm.provider import AIProvider

class ClaudeProvider(AIProvider):
    """Calls Claude API via the official Anthropic Python SDK."""

    def __init__(self, config: dict):
        api_key = os.environ.get("ANTHROPIC_API_KEY") or config.get("api_key")
        if not api_key:
            raise ValueError(
                "Claude API key not found. Set ANTHROPIC_API_KEY environment variable "
                "or add api_key to claude section in config.yaml"
            )
        self.client = Anthropic(api_key=api_key)
        self.model = config.get("model", "claude-haiku-4-5-20251001")
        self.max_tokens = config.get("max_tokens", 1024)
        self.timeout = config.get("timeout", 60)

    def chat(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
        """Send a prompt to Claude and get a response."""

        messages = [{"role": "user", "content": prompt}]

        # If we want JSON, add an instruction to the system prompt
        if json_mode and system_prompt:
            system_prompt += "\n\nReturn ONLY valid JSON. No markdown, no explanation."
        elif json_mode:
            system_prompt = "Return ONLY valid JSON. No markdown, no explanation."

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt if system_prompt else "",
            messages=messages,
        )

        # Extract the text from Claude's response
        return response.content[0].text

    def embed(self, text: str) -> list[float]:
        """Claude doesn't have an embedding endpoint.
        Fall back to sentence-transformers for embeddings."""
        raise NotImplementedError(
            "Claude API does not support embeddings. "
            "Use 'ollama' or 'sentence-transformers' for the embeddings provider."
        )
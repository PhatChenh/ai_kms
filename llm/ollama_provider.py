"""
llm/ollama_provider.py

Ollama implementation of AIProvider.
Calls Ollama's local HTTP API — no API key, no cost, runs offline.

Typical use: embeddings (all tasks), chat tasks you've routed to "ollama".
"""

import requests

from core.config import OllamaConfig
from llm.provider import AIProvider


class OllamaProvider(AIProvider):
    """Calls Ollama's local HTTP API."""

    def __init__(self, config: OllamaConfig) -> None:
        self.base_url        = config.base_url
        self.chat_model      = config.chat_model
        self.embedding_model = config.embedding_model
        self.timeout         = config.timeout

    # ── public interface ──────────────────────────────────────────────────

    def chat(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
        """Send a prompt to Ollama and return the text response."""
        payload: dict = {
            "model":  self.chat_model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if json_mode:
            payload["format"] = "json"

        response = self._post("/api/generate", payload)
        return response.get("response", "")

    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector using Ollama's local model."""
        response = self._post(
            "/api/embeddings",
            {"model": self.embedding_model, "prompt": text},
        )
        return response["embedding"]

    # ── private helpers ───────────────────────────────────────────────────

    def _post(self, endpoint: str, payload: dict) -> dict:
        """
        Make a POST request to Ollama. Raises ConnectionError with a helpful
        message if Ollama is not running, instead of a raw requests exception.
        """
        url = f"{self.base_url}{endpoint}"
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self.base_url}. "
                "Is Ollama running? Try: ollama serve"
            ) from exc
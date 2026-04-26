# llm/ollama_provider.py

import json
import requests
from llm.provider import AIProvider

class OllamaProvider(AIProvider):
    """Calls Ollama's local HTTP API."""

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "http://localhost:11434")
        self.chat_model = config.get("chat_model", "llama3")
        self.embedding_model = config.get("embedding_model", "nomic-embed-text")
        self.timeout = config.get("timeout", 120)

    def chat(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
        """Send a prompt to Ollama and get a response."""

        payload = {
            "model": self.chat_model,
            "prompt": prompt,
            "stream": False,
        }

        if system_prompt:
            payload["system"] = system_prompt

        if json_mode:
            payload["format"] = "json"

        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            result = response.json()
            return result.get("response", "")
        except requests.ConnectionError:
            raise ConnectionError(
                f"Cannot reach Ollama at {self.base_url}. "
                "Is Ollama running? Try: ollama serve"
            )

    def embed(self, text: str) -> list[float]:
        """Generate an embedding vector using Ollama's local model."""

        try:
            response = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except requests.ConnectionError:
            raise ConnectionError(
                f"Cannot reach Ollama at {self.base_url}. "
                "Is Ollama running? Try: ollama serve"
            )
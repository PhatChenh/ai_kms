"""
llm/ollama_provider.py

Ollama implementation of LLMProvider.
Calls Ollama's local HTTP API — no API key, no cost, runs offline.

The sync requests.post() calls are wrapped in asyncio.to_thread() to avoid
blocking the event loop. httpx is not added — asyncio.to_thread is sufficient
for Phase 0 and avoids a new dependency (see plan Out of Scope).
"""

import asyncio

import requests

from core.config import OllamaConfig, Task
from core.result import Failure, Result, Success
from llm.provider import LLMProvider, LLMResponse, SYNTHESIS_TASKS


class OllamaProvider(LLMProvider):
    """Calls Ollama's local HTTP API."""

    def __init__(self, config: OllamaConfig, task: Task = "capture") -> None:
        """
        Initialise the provider.

        Args:
            config: The validated OllamaConfig from MainConfig.
            task:   Pipeline task — used to select model (chat vs synthesis).
        """
        self._base_url = config.base_url
        self._model = config.synthesis_model if task in SYNTHESIS_TASKS else config.chat_model
        self._embedding_model = config.embedding_model
        self._max_tokens = config.max_tokens
        self._timeout = config.timeout

    async def complete(self, system: str, user: str) -> Result[LLMResponse]:
        """
        Send a system + user message pair to Ollama's generate endpoint.

        Args:
            system: Behavioural instructions.
            user:   Content to process.

        Returns:
            Success(LLMResponse) on a valid response.
            Failure(recoverable=True) on ConnectionError or TimeoutError.
        """
        try:
            payload = {
                "model": self._model,
                "prompt": user,
                "system": system,
                "stream": False,
                "format": "json",
                "options": {"num_predict": self._max_tokens},
            }
            raw = await asyncio.to_thread(self._post, "/api/generate", payload)
            return Success(
                LLMResponse(
                    content=raw.get("response", ""),
                    model=self._model,
                    usage={},
                )
            )
        except (ConnectionError, TimeoutError) as exc:
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "ollama"},
            )

    def _post(self, endpoint: str, payload: dict) -> dict:
        """
        Make a synchronous POST request to Ollama.

        Raises:
            TimeoutError: if the request exceeds self._timeout seconds.
            ConnectionError: if Ollama is not reachable, with a helpful message.
        """
        url = f"{self._base_url}{endpoint}"
        try:
            resp = requests.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.Timeout as exc:
            raise TimeoutError(
                f"Ollama at {self._base_url} timed out after {self._timeout}s."
            ) from exc
        except requests.ConnectionError as exc:
            raise ConnectionError(
                f"Cannot reach Ollama at {self._base_url}. "
                "Is Ollama running? Try: ollama serve"
            ) from exc

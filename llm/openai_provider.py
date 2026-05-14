"""
llm/openai_provider.py

Async LLMProvider for any OpenAI-compatible endpoint (Fireworks, Together, etc.).
API key is read from the env var named by config.api_key_env at __init__ time.
"""

import os

import openai

from core.config import OpenAICompatConfig, Task
from core.exceptions import ConfigError
from core.result import Failure, Result, Success
from llm.provider import LLMProvider, LLMResponse, SYNTHESIS_TASKS


class OpenAIProvider(LLMProvider):
    """LLMProvider backed by any OpenAI-compatible REST endpoint."""

    def __init__(self, config: OpenAICompatConfig, task: Task = "capture") -> None:
        """
        Initialise the provider.

        Args:
            config: OpenAICompatConfig from core.config.
            task:   Pipeline task — used to select model (default vs synthesis).

        Raises:
            ConfigError: if the env var named by config.api_key_env is unset or empty.
        """
        api_key = os.environ.get(config.api_key_env)
        if not api_key:
            raise ConfigError(
                f"{config.api_key_env} not set. Add it to .env or export it."
            )
        self._client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout,
        )
        self._model = config.synthesis_model if task in SYNTHESIS_TASKS else config.model
        self._max_tokens = config.max_tokens
        self._embedding_model = config.embedding_model

    async def complete(self, system: str, user: str) -> Result[LLMResponse]:
        """
        Send a system + user message pair to the configured model.

        Args:
            system: Behavioural instructions.
            user:   Content to process.

        Returns:
            Success(LLMResponse) on a valid response.
            Failure(recoverable=True) on any APIError.
        """
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            usage = resp.usage.model_dump() if resp.usage else {}
            return Success(
                LLMResponse(
                    content=resp.choices[0].message.content or "",
                    model=resp.model,
                    usage=usage,
                )
            )
        except openai.APIError as exc:
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "openai_compat", "model": self._model},
            )
        except Exception as exc:
            # httpx / asyncio errors not wrapped by the openai SDK
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "openai_compat", "model": self._model},
            )

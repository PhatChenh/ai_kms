"""
llm/claude_provider.py

Claude implementation of LLMProvider.

The task determines which model is used:
- synthesis / documentation → config.synthesis_model (Sonnet — smarter, slower)
- everything else           → config.model           (Haiku — fast, cheap)

Pipelines don't need to think about model selection; they call
get_provider("synthesis", CONFIG.main) and automatically get the right model.
"""

import os

from anthropic import AsyncAnthropic, AuthenticationError
from anthropic import APIError as AnthropicAPIError
from anthropic.types import TextBlock

from core.config import ClaudeConfig, Task
from core.exceptions import ConfigError
from core.result import Failure, Result, Success
from llm.provider import LLMProvider, LLMResponse, SYNTHESIS_TASKS


class ClaudeProvider(LLMProvider):
    """Calls the Claude API via the official Anthropic async Python SDK."""

    def __init__(self, config: ClaudeConfig, task: Task = "capture") -> None:
        """
        Initialise the provider.

        Args:
            config: The validated ClaudeConfig from MainConfig.
            task:   Pipeline task — used to select model (haiku vs sonnet).

        Raises:
            ConfigError: if ANTHROPIC_API_KEY is unset or empty.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY not set. "
                "Export it in your shell or add it to .env — never put it in config.yaml."
            )
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = config.synthesis_model if task in SYNTHESIS_TASKS else config.model
        self._max_tokens = config.max_tokens
        self._embedding_model = config.embedding_model

    async def complete(self, system: str, user: str) -> Result[LLMResponse]:
        """
        Send a system + user message pair to Claude.

        Args:
            system: Behavioural instructions for the model.
            user:   Content to process.

        Returns:
            Success(LLMResponse) on a valid response.
            Failure(recoverable=False) on AuthenticationError.
            Failure(recoverable=True) on any other APIError.
        """
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            usage = resp.usage.model_dump() if resp.usage else {}
            text_block = next((b for b in resp.content if isinstance(b, TextBlock)), None)
            content = text_block.text if text_block else ""
            return Success(
                LLMResponse(
                    content=content,
                    model=resp.model,
                    usage=usage,
                )
            )
        except AuthenticationError as exc:
            return Failure(
                error=str(exc),
                recoverable=False,
                context={"provider": "claude"},
            )
        except AnthropicAPIError as exc:
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "claude"},
            )
        except Exception as exc:
            # httpx / network errors not wrapped by the Anthropic SDK
            return Failure(
                error=str(exc),
                recoverable=True,
                context={"provider": "claude"},
            )

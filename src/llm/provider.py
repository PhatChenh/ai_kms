"""
llm/provider.py

Abstract base class for LLM providers + factory function.

Usage:
    from core.config import CONFIG
    from llm.provider import get_provider

    provider = get_provider("capture", CONFIG.main)
    result = await provider.complete(system="...", user="...")
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from core.config import MainConfig, Provider, Task
from core.result import Failure, Result

# Tasks that route to the synthesis (smarter) model on every provider.
# Defined here so all providers share one source of truth.
SYNTHESIS_TASKS: frozenset[Task] = frozenset({"synthesis", "documentation"})


@dataclass(frozen=True)
class LLMResponse:
    """Immutable response DTO returned by every LLMProvider.complete() call."""
    content: str
    model: str
    usage: dict


class LLMProvider(ABC):
    """
    Interface every LLM provider must satisfy.
    Callers only speak to this class — never to concrete providers directly.
    """

    @abstractmethod
    async def complete(self, system: str, user: str) -> Result[LLMResponse]:
        """
        Send a system + user message pair to the model.

        Args:
            system: Behavioural instructions for the model.
            user:   The user message / content to process.

        Returns:
            Success(LLMResponse) on a valid response,
            Failure on any API or network error.
        """

    async def describe_image(self, system: str, user: str, image_bytes: bytes, mime_type: str) -> Result[LLMResponse]:
        """Describe an image. Default: returns Failure — override for vision-capable providers."""
        return Failure("vision not supported by this provider", recoverable=False, context={})


def get_provider(task: Task, config: MainConfig) -> LLMProvider:
    """
    Factory: return the correct LLMProvider for a pipeline task.

    Args:
        task:   One of the Task literals ("classify", "synthesis", etc.)
        config: The validated MainConfig singleton.

    Returns:
        An LLMProvider ready to call.

    Raises:
        ValueError: if the configured provider name is unknown.
    """
    provider_name: Provider = config.providers.for_task(task)

    match provider_name:
        case "claude":
            from llm.claude_provider import ClaudeProvider
            return ClaudeProvider(config.claude, task=task)
        case "claude_cli":
            from llm.claude_cli_provider import ClaudeCliProvider
            return ClaudeCliProvider(config.claude_cli, task=task)
        case "ollama":
            from llm.ollama_provider import OllamaProvider
            return OllamaProvider(config.ollama, task=task)
        case "openai":
            from llm.openai_provider import OpenAIProvider
            return OpenAIProvider(config.openai_compat, task=task)
        case _:
            raise ValueError(
                f"Unknown provider '{provider_name}' for task '{task}'. "
                f"Valid options: 'claude', 'claude_cli', 'ollama', 'openai'."
            )

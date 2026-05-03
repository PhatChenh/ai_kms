"""
llm/provider.py

Abstract base class for AI providers + factory function.

Pattern: Handler Registry applied to LLM providers.
- Each provider implements the same interface (AIProvider ABC).
- The factory get_provider() selects the right one based on config.
- Adding a new provider = add a new class, register it in the factory. Nothing else changes.

Usage:
    from core.config import CONFIG
    from llm.provider import get_provider

    provider = get_provider("classify", CONFIG.main)
    response = provider.chat(prompt="...", system_prompt="...")
"""

from abc import ABC, abstractmethod

from core.config import ClaudeConfig, MainConfig, OllamaConfig, Provider, Task


class AIProvider(ABC):
    """
    Interface every LLM provider must satisfy.
    Callers only speak to this class — never to ClaudeProvider or OllamaProvider directly.
    """

    @abstractmethod
    def chat(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
        """
        Send a prompt to the model and return a text response.

        Args:
            prompt:        The user message / content to process.
            system_prompt: Behavioural instructions for the model.
            json_mode:     If True, instruct the model to return valid JSON only.

        Returns:
            The model's response as a plain string.
        """

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """
        Convert text into an embedding vector for semantic search.

        Args:
            text: The text to embed.

        Returns:
            A list of floats representing the embedding.
        """


def get_provider(task: Task, config: MainConfig) -> AIProvider:
    """
    Factory: return the correct AIProvider for a pipeline task.

    Reads which provider to use from config.providers.for_task(task),
    then instantiates that provider with its section of the config.

    Args:
        task:   One of the Task literals ("classify", "synthesis", etc.)
        config: The validated MainConfig singleton.

    Returns:
        An AIProvider ready to call.

    Raises:
        ValueError: if the configured provider name is unknown.

    Usage:
        provider = get_provider("synthesis", CONFIG.main)
        text = provider.chat(prompt, system_prompt)
    """
    provider_name: Provider = config.providers.for_task(task)

    match provider_name:
        case "claude":
            from llm.claude_provider import ClaudeProvider
            return ClaudeProvider(config.claude, task=task)
        case "ollama":
            from llm.ollama_provider import OllamaProvider
            return OllamaProvider(config.ollama)
        case _:
            raise ValueError(
                f"Unknown provider '{provider_name}' for task '{task}'. "
                f"Valid options: 'claude', 'ollama'."
            )
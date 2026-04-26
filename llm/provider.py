# llm/provider.py

from abc import ABC, abstractmethod

class AIProvider(ABC):
    """Base class for AI providers. Both Claude and Ollama implement this."""

    @abstractmethod
    def chat(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> str:
        """Send a prompt to the AI and get a text response back.

        Args:
            prompt: The user message / main content to process
            system_prompt: Instructions for how the AI should behave
            json_mode: If True, ask the AI to return valid JSON

        Returns:
            The AI's response as a string
        """
        pass

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Convert text into a numerical vector (embedding) for semantic search.

        Args:
            text: The text to embed

        Returns:
            A list of floats (the embedding vector)
        """
        pass


def get_provider(task: str, config: dict) -> AIProvider:
    """Factory function that returns the right provider for a given task.

    Args:
        task: One of "classify", "synthesis", "embeddings", "self_learn"
        config: The parsed config.yaml dictionary

    Returns:
        An AIProvider instance (either ClaudeProvider or OllamaProvider)
    """
    provider_name = config["providers"].get(task, "ollama")

    if provider_name == "claude":
        from llm.claude_provider import ClaudeProvider
        return ClaudeProvider(config["claude"])
    elif provider_name == "ollama":
        from llm.ollama_provider import OllamaProvider
        return OllamaProvider(config["ollama"])
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
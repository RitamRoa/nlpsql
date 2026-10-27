from typing import Protocol


class ProviderError(Exception):
    """Raised when an LLM provider fails."""

    pass


class LLMProvider(Protocol):
    def generate_text(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str: ...

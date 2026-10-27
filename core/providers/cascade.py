from .base import LLMProvider, ProviderError


class CascadingProvider:
    """Tries a fast primary provider first.

    If it fails, automatically falls back to a secondary provider.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider):
        self.primary = primary
        self.fallback = fallback
        self.last_provider_used: str = "primary"

    def generate_text(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str:
        try:
            res = self.primary.generate_text(
                prompt,
                stop=stop,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            self.last_provider_used = "primary"
            return res
        except Exception:
            # Fall back to secondary provider
            res = self.fallback.generate_text(
                prompt,
                stop=stop,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            self.last_provider_used = "fallback"
            return res

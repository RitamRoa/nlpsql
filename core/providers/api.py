import json
import os
import urllib.request
import urllib.error
from .base import LLMProvider, ProviderError


class HTTPAPIProvider:
    """Generic OpenAI / Groq / OpenRouter / DeepSeek compatible HTTP API provider.

    Zero extra dependencies; uses Python's standard urllib.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        endpoint: str = "https://api.openai.com/v1/chat/completions",
        timeout_s: float = 20.0,
    ):
        self.api_key = (
            api_key
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("LLM_API_KEY")
        )
        self.model = model
        self.endpoint = endpoint
        self.timeout_s = timeout_s

    def generate_text(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str:
        if not self.api_key:
            raise ProviderError(
                "API key missing. Set OPENAI_API_KEY or LLM_API_KEY environment variable."
            )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a specialized Text-to-SQL assistant. Respond only with executable SQL.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stop": stop or [";"],
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                choice = result["choices"][0]
                content = choice.get("message", {}).get("content", "").strip()
                return content
        except Exception as e:
            raise ProviderError(f"HTTP API error ({self.endpoint}): {e}") from e

"""Environment-driven local-first provider construction."""
import os

from .base import LLMProvider
from .mock import MockProvider


def configured_provider() -> LLMProvider:
    ollama_host = os.environ.get("OLLAMA_HOST")
    if ollama_host:
        from .ollama import OllamaProvider

        return OllamaProvider(
            host=ollama_host,
            model=os.environ.get("OLLAMA_MODEL", "smollm2:135m"),
        )

    model_path = os.environ.get(
        "MODEL_PATH", "SmolLM2-135M-text-to-sql.Q8_0.gguf"
    )
    if os.path.exists(model_path) and os.environ.get("MOCK_LLM", "0") != "1":
        from ..generator import SQLGenerator

        return SQLGenerator(model_path=model_path)

    if os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY"):
        from .api import HTTPAPIProvider

        return HTTPAPIProvider(
            model=os.environ.get("LLM_API_MODEL", "gpt-4o-mini"),
            endpoint=os.environ.get(
                "LLM_API_ENDPOINT",
                "https://api.openai.com/v1/chat/completions",
            ),
        )
    return MockProvider()

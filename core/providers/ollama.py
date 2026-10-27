import json
import urllib.request
import urllib.error
from .base import LLMProvider, ProviderError


class OllamaProvider:
    """Connects to a locally running Ollama instance via HTTP (no C++ build tools required)."""

    def __init__(
        self,
        model: str = "smollm2:135m",
        host: str = "http://localhost:11434",
        timeout_s: float = 30.0,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout_s = timeout_s

    def generate_text(
        self,
        prompt: str,
        stop: list[str] | None = None,
        max_tokens: int = 150,
        temperature: float = 0.0,
    ) -> str:
        url = f"{self.host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "stop": stop or [],
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result.get("response", "").strip()
        except urllib.error.URLError as e:
            raise ProviderError(
                f"Failed to connect to Ollama at {self.host}: {e}. "
                f"Make sure Ollama is installed and running (ollama serve)."
            ) from e
        except Exception as e:
            raise ProviderError(f"Ollama generation error: {e}") from e

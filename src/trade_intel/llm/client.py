"""Minimal Ollama HTTP client — stdlib only, zero new dependencies.

Talks to a locally-running ``ollama serve`` (default ``http://127.0.0.1:11434``)
over its REST API. No paid APIs, no SDK: just ``urllib``.

Design goals (cheap-VPS / low-RAM target):

- **CPU-friendly**: passes ``num_thread`` + ``num_ctx`` + ``num_predict``
  options so a $5-10 VPS can run qwen3:4b without thrashing.
- **Low-RAM**: short ``keep_alive`` unloads the model between bursts; a small
  ``num_ctx`` keeps the KV-cache tiny.
- **Resilient**: a primary model that 404s (not pulled) transparently falls
  back to the configured fallback model.
- **Streaming or one-shot**: :meth:`generate` returns the whole string;
  :meth:`stream` yields token chunks for WebSocket/CLI live output.

The production Go backend will speak the same REST API; this Python client
exists for the validation harness and the Python ``trade-intel chat`` path.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

from trade_intel.config import get_settings
from trade_intel.core.logging import get_logger

log = get_logger(__name__, component="llm")


class OllamaError(RuntimeError):
    """Ollama is unreachable, the model is missing, or a request failed."""


@dataclass(slots=True)
class LlmResult:
    """One completed generation plus the timing/usage telemetry we report."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_s: float = 0.0
    load_duration_s: float = 0.0
    eval_duration_s: float = 0.0

    @property
    def tokens_per_second(self) -> float:
        if self.eval_duration_s <= 0 or self.completion_tokens <= 0:
            return 0.0
        return self.completion_tokens / self.eval_duration_s

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_duration_s": round(self.total_duration_s, 3),
            "load_duration_s": round(self.load_duration_s, 3),
            "eval_duration_s": round(self.eval_duration_s, 3),
            "tokens_per_second": round(self.tokens_per_second, 2),
        }


@dataclass(slots=True)
class OllamaClient:
    """Thin wrapper over the Ollama ``/api/generate`` + ``/api/tags`` routes."""

    host: str = ""
    model: str = ""
    fallback_model: str = ""
    num_ctx: int = 0
    num_predict: int = 0
    num_thread: int = 0
    keep_alive: str = ""
    temperature: float = -1.0
    timeout: float = 0.0
    _resolved_model: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        s = get_settings()
        self.host = (self.host or s.ollama_host).rstrip("/")
        self.model = self.model or s.llm_model
        self.fallback_model = self.fallback_model or s.llm_fallback_model
        self.num_ctx = self.num_ctx or s.llm_num_ctx
        self.num_predict = self.num_predict or s.llm_num_predict
        self.num_thread = self.num_thread or s.llm_num_thread
        self.keep_alive = self.keep_alive or s.llm_keep_alive
        if self.temperature < 0:
            self.temperature = s.llm_temperature
        self.timeout = self.timeout or s.llm_timeout

    # ----------------------------------------------------------- introspection

    def _get(self, path: str) -> dict:
        url = f"{self.host}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except urllib.error.URLError as e:  # connection refused / DNS / timeout
            raise OllamaError(
                f"cannot reach Ollama at {self.host} ({e}). "
                "Is `ollama serve` running?"
            ) from e

    def available_models(self) -> list[str]:
        """Names of all locally-pulled models (``ollama list`` equivalent)."""
        data = self._get("/api/tags")
        return [m["name"] for m in data.get("models", [])]

    def health(self) -> bool:
        try:
            self.available_models()
            return True
        except OllamaError:
            return False

    def resolve_model(self) -> str:
        """Pick the primary model if pulled, else the fallback, else raise."""
        if self._resolved_model:
            return self._resolved_model
        pulled = self.available_models()
        # Ollama tags carry a ``:latest`` suffix when none is given; match loosely.
        for candidate in (self.model, self.fallback_model):
            for name in pulled:
                if name == candidate or name.split(":")[0] == candidate.split(":")[0]:
                    if candidate != self.model:
                        log.warning(
                            "llm_model_fallback",
                            requested=self.model,
                            using=name,
                        )
                    self._resolved_model = name
                    return name
        raise OllamaError(
            f"neither {self.model!r} nor fallback {self.fallback_model!r} "
            f"is pulled. Available: {pulled or '[]'}. "
            f"Run: ollama pull {self.model}"
        )

    # ----------------------------------------------------------- options

    def _options(self) -> dict[str, object]:
        opts: dict[str, object] = {
            "num_ctx": self.num_ctx,
            "num_predict": self.num_predict,
            "temperature": self.temperature,
        }
        if self.num_thread > 0:
            opts["num_thread"] = self.num_thread
        return opts

    # ----------------------------------------------------------- generate

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> LlmResult:
        """One-shot completion. Blocks until the full answer is generated."""
        use_model = model or self.resolve_model()
        body = {
            "model": use_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self._options(),
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise OllamaError(f"generate failed ({e.code}): {detail}") from e
        except urllib.error.URLError as e:
            raise OllamaError(
                f"generate failed talking to {self.host}: {e}"
            ) from e
        wall = time.monotonic() - started
        ns = 1e9
        return LlmResult(
            text=data.get("response", "").strip(),
            model=use_model,
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            completion_tokens=int(data.get("eval_count", 0)),
            total_duration_s=data.get("total_duration", 0) / ns or wall,
            load_duration_s=data.get("load_duration", 0) / ns,
            eval_duration_s=data.get("eval_duration", 0) / ns,
        )

    def stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        """Yield response chunks as they are generated (for live CLI/WS output)."""
        use_model = model or self.resolve_model()
        body = {
            "model": use_model,
            "prompt": prompt,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": self._options(),
        }
        if system:
            body["system"] = system
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    piece = chunk.get("response", "")
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except urllib.error.URLError as e:
            raise OllamaError(f"stream failed talking to {self.host}: {e}") from e


__all__ = ["LlmResult", "OllamaClient", "OllamaError"]

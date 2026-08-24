"""LLM provider abstraction.

Swap providers with one env var. `mock` is deterministic and offline, which
keeps the demo alive when an API rate-limits at the worst possible moment.
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Provider failed or returned output that did not match the schema."""


@dataclass
class LLMResult:
    """Observability envelope around every model call."""
    output: BaseModel
    provider: str
    model: str
    latency_ms: int
    degraded: bool = False
    meta: dict = field(default_factory=dict)


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def _generate(self, system: str, prompt: str, schema: type[T]) -> T: ...

    def generate(self, system: str, prompt: str, schema: type[T]) -> LLMResult:
        started = time.perf_counter()
        try:
            output = self._generate(system, prompt, schema)
        except ValidationError as exc:
            raise LLMError(f"Model output failed schema validation: {exc}") from exc
        return LLMResult(
            output=output,
            provider=self.name,
            model=getattr(self, "model", "n/a"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def get_llm_provider(name: str | None = None) -> LLMProvider:
    provider = (name or settings.LLM_PROVIDER).lower()

    if provider == "gemini":
        from app.services.llm.gemini import GeminiProvider
        if not settings.GEMINI_API_KEY:
            from app.services.llm.mock import MockProvider
            return MockProvider()  # no key configured — stay demoable
        return GeminiProvider()

    from app.services.llm.mock import MockProvider
    return MockProvider()

from dataclasses import dataclass, field
from typing import Protocol


class AiError(RuntimeError):
    """The provider could not answer (network, auth, bad output). Never fatal to the pipeline."""


@dataclass(frozen=True)
class Decision:
    """Structured output: `fields` match the requested schema, `confidence` is 0..1."""

    fields: dict
    confidence: float
    provider: str
    model: str = ""
    latency_ms: int = 0
    input_chars: int = 0
    raw: dict = field(default_factory=dict)


class AiProvider(Protocol):
    name: str

    def decide(self, *, task: str, input: str, schema: dict) -> Decision:
        """Turn `input` into the fields of `schema` (a JSON Schema object) following `task`."""
        ...

"""Claude through the official Anthropic SDK with structured outputs (output_config.format).

CLAUDE_MODEL defaults to claude-opus-5-5. ANTHROPIC_API_KEY (or an `ant auth
login` profile) authenticates. The requested schema gets a `confidence`
number added so every answer carries one, like Jev's.
"""

import json
import os
import time

from .base import AiError, Decision


class ClaudeProvider:
    name = "claude"

    def __init__(self, *, model: str | None = None, client=None):
        self.model = model or os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover
                raise AiError("the anthropic package is not installed") from e
            client = anthropic.Anthropic()
        self.client = client

    def decide(self, *, task: str, input: str, schema: dict) -> Decision:
        import anthropic

        props = dict(schema.get("properties", {}))
        props["confidence"] = {
            "type": "number",
            "description": "0..1, how sure you are of the whole answer",
        }
        full_schema = {
            "type": "object",
            "properties": props,
            "required": sorted(set(schema.get("required", [])) | {"confidence"}),
            "additionalProperties": False,
        }
        started = time.monotonic()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=(
                    "You supervise live-show sales for a small shop. Answer only with the JSON "
                    "the schema asks for. Be conservative: when the chat does not clearly support "
                    "a conclusion, say so with a low confidence rather than guessing."
                ),
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": full_schema},
                },
                messages=[{"role": "user", "content": f"{task.strip()}\n\n{input}"}],
            )
        except anthropic.AuthenticationError as e:
            raise AiError("claude: API key rejected") from e
        except anthropic.RateLimitError as e:
            raise AiError("claude: rate limited") from e
        except anthropic.APIStatusError as e:
            raise AiError(f"claude: HTTP {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise AiError(f"claude: {e}") from e
        latency = int((time.monotonic() - started) * 1000)
        if response.stop_reason == "refusal":
            raise AiError("claude: request refused by safety classifiers")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except ValueError as e:
            raise AiError("claude: answer is not JSON") from e
        confidence = data.pop("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0
        return Decision(
            fields=data,
            confidence=confidence,
            provider=self.name,
            model=self.model,
            latency_ms=latency,
            input_chars=len(input) + len(task),
            raw={"stop_reason": response.stop_reason},
        )

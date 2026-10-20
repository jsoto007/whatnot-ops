"""Jev (jevai.net): `POST /v1/decide` with `input` + `schema`, answers the fields plus `confidence`.

Early access: the public site documents the shape but not auth, so the
header name is configurable. Set JEV_API_URL, JEV_API_KEY (and optionally
JEV_MODEL, JEV_AUTH_HEADER=Authorization). Any transport or shape problem is
an AiError; the caller decides what to do without an answer.
"""

import os
import time

import requests

from .base import AiError, Decision


class JevProvider:
    name = "jev"

    def __init__(
        self, *, url: str | None = None, key: str | None = None, model: str | None = None, post=None
    ):
        self.url = (url or os.environ.get("JEV_API_URL", "https://api.jevai.net")).rstrip("/")
        self.key = key or os.environ.get("JEV_API_KEY", "")
        self.model = model or os.environ.get("JEV_MODEL", "")
        self.auth_header = os.environ.get("JEV_AUTH_HEADER", "Authorization")
        self._post = post or requests.post
        if not self.key:
            raise AiError("JEV_API_KEY is not set")

    def decide(self, *, task: str, input: str, schema: dict) -> Decision:
        body: dict = {"input": f"{task.strip()}\n\n{input}", "schema": schema}
        if self.model:
            body["model"] = self.model
        headers = {
            self.auth_header: ("Bearer " + self.key)
            if self.auth_header == "Authorization"
            else self.key
        }
        started = time.monotonic()
        try:
            r = self._post(self.url + "/v1/decide", json=body, headers=headers, timeout=30)
        except requests.RequestException as e:
            raise AiError(f"jev: {e}") from e
        latency = int((time.monotonic() - started) * 1000)
        if r.status_code == 401:
            raise AiError("jev: API key rejected")
        if r.status_code >= 300:
            raise AiError(f"jev: HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise AiError("jev: response is not JSON") from e
        if not isinstance(data, dict):
            raise AiError("jev: unexpected response shape")
        confidence = data.get("confidence")
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = 0.0
        fields = {k: v for k, v in data.items() if k != "confidence"}
        missing = [k for k in schema.get("required", []) if k not in fields]
        if missing:
            raise AiError(f"jev: answer is missing {missing}")
        return Decision(
            fields=fields,
            confidence=confidence,
            provider=self.name,
            model=self.model,
            latency_ms=latency,
            input_chars=len(body["input"]),
            raw=data,
        )

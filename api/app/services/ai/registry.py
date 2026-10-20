"""Which provider to use, from AI_PROVIDER: jev | claude | fake | none (default)."""

import os

from flask import current_app

from .base import AiError, AiProvider


def provider_name() -> str:
    return (os.environ.get("AI_PROVIDER") or "none").strip().lower()


def get_provider() -> AiProvider | None:
    """The configured provider, or None when AI is off. AiError when configured but broken."""
    name = provider_name()
    if name in ("", "none", "off"):
        return None
    if name == "fake":
        if current_app.config.get("APP_ENV") == "production":
            raise AiError("AI_PROVIDER=fake is refused in production")
        from .fake import FakeProvider

        return FakeProvider()
    if name == "jev":
        from .jev import JevProvider

        return JevProvider()
    if name == "claude":
        from .claude import ClaudeProvider

        return ClaudeProvider()
    raise AiError(f"unknown AI_PROVIDER {name!r}")

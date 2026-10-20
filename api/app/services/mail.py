"""Transactional email through Mailgun's REST API; without MAILGUN_API_KEY the message is logged."""

import logging
import os
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None


class MailError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("MAILGUN_API_KEY") and os.environ.get("MAILGUN_DOMAIN"))


def send(msg: Message, *, post=None) -> bool:
    """Returns True when handed to Mailgun, False when only logged."""
    post = post or requests.post  # resolved at call time so tests can patch requests.post
    if not configured():
        log.info(
            "EMAIL (not sent, Mailgun unset) to=%s subject=%r\n%s", msg.to, msg.subject, msg.text
        )
        return False
    domain = os.environ["MAILGUN_DOMAIN"]
    base = os.environ.get("MAILGUN_API_BASE", "https://api.mailgun.net/v3")
    sender = os.environ.get("EMAIL_FROM", f"Whatnot Ops <noreply@{domain}>")
    data = {"from": sender, "to": msg.to, "subject": msg.subject, "text": msg.text}
    if msg.html:
        data["html"] = msg.html
    try:
        r = post(
            f"{base}/{domain}/messages",
            auth=("api", os.environ["MAILGUN_API_KEY"]),
            data=data,
            timeout=10,
        )
    except requests.RequestException as e:  # network
        raise MailError(str(e)) from e
    if r.status_code >= 300:
        raise MailError(f"Mailgun returned {r.status_code}")
    return True

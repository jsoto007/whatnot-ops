"""The emails Sales Ops sends, each in one branded layout plus a plain-text twin.

One layout for every message: the logo header, a heading, a short explanation,
one button, the expiry, and the link spelled out for clients that strip
buttons. Tables and inline styles only — Gmail, Outlook and Apple Mail agree
on little else. Colours mirror web/src/styles/tokens.css.

Everything that came from a person (names, shop, role, the link) is escaped:
an invite is sent to a stranger and carries the inviter's chosen names.
"""

from html import escape
from pathlib import Path
from string import Template

from . import mail

APP_NAME = "Sales Ops"

# tokens.css
_NAVY = "#0b1f3a"
_NAVY_INK = "#071428"
_GREEN = "#69be28"
_BG = "#f4f6fa"
_SURFACE = "#ffffff"
_SURFACE_2 = "#f8fafc"
_TEXT = "#0f172a"
_BODY = "#334155"
_MUTED = "#5b6b82"
_FAINT = "#8a97aa"
_BORDER = "#e3e8ef"
_FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, Helvetica, Arial, sans-serif"
_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
_COLOURS = {
    "navy": _NAVY,
    "navy_ink": _NAVY_INK,
    "green": _GREEN,
    "bg": _BG,
    "surface": _SURFACE,
    "surface_2": _SURFACE_2,
    "text": _TEXT,
    "muted": _MUTED,
    "faint": _FAINT,
    "border": _BORDER,
    "font": _FONT,
    "mono": _MONO,
}
# The layout lives beside this file: tables and inline styles are long lines.
_LAYOUT = Template(Path(__file__).with_name("mail_layout.html").read_text(encoding="utf-8"))


def _app_url(path: str) -> str:
    from .accounts import app_url  # accounts imports mail; keep the import one-way at load

    return app_url(path)


def render(
    *,
    preheader: str,
    heading: str,
    paragraphs: list[str],
    button_label: str,
    button_url: str,
    expiry: str,
    footer: str,
) -> str:
    """The HTML body (mail_layout.html). `paragraphs` and `footer` may contain
    <b> tags from this module only; every dynamic value inside them must
    already be escaped by the caller. The rest is escaped here."""
    body = "".join(
        f'<p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:{_BODY};">{p}</p>'
        for p in paragraphs
    )
    return _LAYOUT.substitute(
        _COLOURS,
        app_name=APP_NAME,
        logo=escape(_app_url("/logo.png"), quote=True),
        home=escape(_app_url("/"), quote=True),
        url=escape(button_url, quote=True),
        preheader=escape(preheader),
        heading=escape(heading),
        body=body,
        button_label=escape(button_label),
        expiry=escape(expiry),
        footer=footer,
    )


def _plain(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _days(n: int) -> str:
    return f"{n} day" if n == 1 else f"{n} days"


def _hours(n: int) -> str:
    return f"{n} hour" if n == 1 else f"{n} hours"


def invite(
    *, to: str, inviter_name: str, organization: str, role: str, url: str, days: int
) -> mail.Message:
    inviter = escape(inviter_name)
    org = escape(organization)
    return mail.Message(
        to=to,
        subject=f"{inviter_name} invited you to join {organization} on {APP_NAME}",
        text=_plain(
            [
                "Hi,",
                "",
                f"{inviter_name} invited you to join {organization} on {APP_NAME} as {role}.",
                "",
                "Accept the invite:",
                url,
                "",
                f"This link works for {_days(days)}. If you weren't expecting it, you can ignore "
                "this email.",
                "",
                f"— {APP_NAME}",
            ]
        ),
        html=render(
            preheader=f"{inviter_name} invited you to join {organization}.",
            heading=f"You're invited to join {organization}",
            paragraphs=[
                f"<b>{inviter}</b> invited you to join <b>{org}</b> on {APP_NAME} as "
                f"<b>{escape(role)}</b>.",
                "Accept the invite to set your password and start working shows with the team.",
            ],
            button_label="Accept invite",
            button_url=url,
            expiry=f"This link works for {_days(days)}.",
            footer=(
                f"You received this email because {inviter} invited {escape(to)} to {org}. "
                "If you weren't expecting it, you can ignore this email."
            ),
        ),
    )


def password_reset(*, to: str, url: str, hours: int) -> mail.Message:
    return mail.Message(
        to=to,
        subject=f"Reset your {APP_NAME} password",
        text=_plain(
            [
                "Hi,",
                "",
                f"We received a request to reset the password for {to} on {APP_NAME}.",
                "",
                "Choose a new password:",
                url,
                "",
                f"This link works for {_hours(hours)} and can be used once. If you didn't ask "
                "for this, you can ignore this email — your password stays the same.",
                "",
                f"— {APP_NAME}",
            ]
        ),
        html=render(
            preheader=f"Choose a new password for {to}.",
            heading="Reset your password",
            paragraphs=[
                f"We received a request to reset the password for <b>{escape(to)}</b> on "
                f"{APP_NAME}.",
                "Use the button below to choose a new one. Nothing changes until you do.",
            ],
            button_label="Reset password",
            button_url=url,
            expiry=f"This link works for {_hours(hours)} and can be used once.",
            footer=(
                "If you didn't ask to reset your password, you can ignore this email — "
                "your password stays the same."
            ),
        ),
    )

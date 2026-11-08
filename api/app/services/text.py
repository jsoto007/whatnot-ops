"""Text from outside (chat captured by the extension, uploaded payment and stock files) is
stored and shown as plain text, never run or rendered as markup. Before it is stored it loses
what can hide or disguise it: control characters, and the invisible Unicode marks that reorder
or hide text (a viewer could otherwise make "won the auction" read differently in Review than
what the parser saw, or hide a second name inside a handle).

Emoji sequences keep their zero-width joiner (U+200D); nothing else is rewritten.
"""

import re
from urllib.parse import urlsplit

# C0 and C1 controls except tab / newline / carriage return, which callers fold or keep.
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Bidi embeddings / overrides / isolates, directional marks, zero-width space, word joiner,
# invisible operators, BOM and the interlinear annotation marks.
_INVISIBLE = re.compile(
    r"[\u200b\u200e\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\ufff9-\ufffb]"
)
_LINEBREAKS = re.compile(r"[\t\r\n\u2028\u2029\u0085]+")
_SPACES = re.compile(r"[ \u00a0\u2000-\u200a\u3000]{2,}")


def clean(s: str | None, *, single_line: bool = True) -> str | None:
    """Drop controls, invisible marks and lone surrogates (Postgres refuses NUL and
    surrogates). A single line also folds tabs / line breaks into one space."""
    if s is None:
        return None
    s = _INVISIBLE.sub("", _CONTROLS.sub("", s))
    if single_line:
        # Runs of spaces too: they carry nothing and feed regex backtracking downstream.
        s = _SPACES.sub(" ", _LINEBREAKS.sub(" ", s))
    return s.encode("utf-8", "replace").decode("utf-8")


def typed(value, limit: int | None = None) -> str:
    """A name someone typed, as models/clean.py will store it: cleaned, trimmed, cut to
    `limit`. "Required" and duplicate checks use this, so a name of only invisible marks
    is empty, and a tab or a bell does not make a "different" name that then fails the
    unique index with a 500."""
    s = (clean(value) if isinstance(value, str) else "").strip()
    return s[:limit] if limit else s


def has_hidden(s: str) -> bool:
    """Control characters, invisible marks or lone surrogates. An identifier holding them
    (a show key) is refused, not rewritten: lookups must match what is stored, and
    Postgres refuses NUL and surrogates with an error instead of a row."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return bool(_CONTROLS.search(s) or _INVISIBLE.search(s))


def clean_file(s: str) -> str:
    """An uploaded CSV: keep its line structure, clean everything else."""
    return clean(s.replace("\r\n", "\n").replace("\r", "\n"), single_line=False) or ""


def whatnot_url(url: str | None) -> str | None:
    """A show's address: the page path the extension sends ("/live/…"), or a https link to
    Whatnot itself. Anything else (javascript:, another site) is not kept."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return clean(url)
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not (host == "whatnot.com" or host.endswith(".whatnot.com")):
        return None
    return clean(url)

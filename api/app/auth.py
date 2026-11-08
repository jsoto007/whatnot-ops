"""Session cookie auth for the SPA + session-bound CSRF token + permission decorator."""

import secrets
from functools import wraps

from flask import current_app, g, jsonify, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .db import db
from .models import User
from .services import ratelimit
from .services.permissions import allowed

CSRF_HEADER = "X-CSRF-Token"
CSRF_KEY = "csrf"  # in the signed session cookie
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
#: Signed "this browser has signed in to <email> before" marker. It only lifts the
#: per-email failed-login cap (services/accounts.login); it grants no access.
KNOWN_DEVICE_COOKIE = "wn_known"
KNOWN_DEVICE_DAYS = 180


def csrf_ok() -> bool:
    """The header must match the token kept in this browser's signed session. It used to
    match a plain `csrf` cookie, which a sibling subdomain (on a custom domain) could set
    for the whole domain, then send the same value as the header from its own page. A
    header with non-ASCII characters is simply wrong (403), not a TypeError (500)."""
    expected = session.get(CSRF_KEY)
    header = request.headers.get(CSRF_HEADER)
    if not isinstance(expected, str) or not header or not header.isascii():
        return False
    return secrets.compare_digest(expected, header)


def _known_signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="known-device")


def _known_tags() -> list[str]:
    raw = request.cookies.get(KNOWN_DEVICE_COOKIE)
    if not raw or len(raw) > 512:
        return []
    try:
        tags = _known_signer().loads(raw, max_age=KNOWN_DEVICE_DAYS * 86400)
    except BadSignature:
        return []
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


def known_device_tags() -> frozenset[str]:
    """The accounts this browser has signed in to (see accounts.device_tag)."""
    return frozenset(_known_tags())


def remember_device(resp, user: User):
    """Mark this browser as one `user` has signed in from (a few accounts per browser)."""
    from .services.accounts import device_tag

    tag = device_tag(user)
    tags = ([tag] + [t for t in _known_tags() if t != tag])[:4]
    resp.set_cookie(
        KNOWN_DEVICE_COOKIE,
        _known_signer().dumps(tags),
        samesite="Lax",
        secure=request.is_secure or current_app.config.get("APP_ENV") == "production",
        httponly=True,
        max_age=KNOWN_DEVICE_DAYS * 86400,
        path="/api/auth",
    )
    return resp


def current_user() -> User | None:
    if "u" in g:
        return g.u
    uid = session.get("uid")
    tok = session.get("tok")
    user = db.session.get(User, uid) if uid else None
    if user is None or user.disabled or user.session_token != tok:
        user = None
    g.u = user
    return user


def start_session(user: User) -> None:
    _clear_session_keep_csrf()
    session["uid"] = user.id
    session["tok"] = user.session_token
    session.permanent = True
    g.u = user


def end_session() -> None:
    _clear_session_keep_csrf()
    g.u = None


def _clear_session_keep_csrf() -> None:
    """Signing in or out keeps the page's CSRF token: only same-origin script can read it
    (GET /api/auth/csrf), so it does not need to change with the account."""
    token = session.get(CSRF_KEY)
    session.clear()
    if isinstance(token, str):
        session[CSRF_KEY] = token
        session.permanent = True


def csrf_token() -> str:
    """This browser's token, made on first request and kept in the signed session."""
    token = session.get(CSRF_KEY)
    if not (isinstance(token, str) and len(token) == 43):
        token = secrets.token_urlsafe(32)
        session[CSRF_KEY] = token
        session.permanent = True
    return token


# Changes one signed-in person may make per minute (see require).
WRITES_PER_MIN = 300
# Loads of the expensive pages (dashboards, order lists, slips, review) per person per
# minute, all of them together. The pages poll every 30 s; this stops a script with a
# stolen session from keeping the small instance busy.
HEAVY_READS_PER_MIN = 120


def require(permission: str | None = None, *, heavy: bool = False):
    """Route needs a logged-in user (and a permission). Also enforces CSRF on mutating
    requests. `heavy` marks a costly read: it counts toward HEAVY_READS_PER_MIN."""

    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if user is None:
                return jsonify({"error": "not signed in"}), 401
            if request.method not in SAFE_METHODS:
                if not csrf_ok():
                    return jsonify({"error": "missing or invalid CSRF token"}), 403
                # Generous for a person clicking; stops a script with a stolen session.
                ratelimit.per_user("write", user.id, WRITES_PER_MIN, 60)
            elif heavy:
                ratelimit.per_user("heavy-read", user.id, HEAVY_READS_PER_MIN, 60)
            if permission and not allowed(user.role, permission):
                return jsonify({"error": "you do not have permission to do that"}), 403
            return fn(*a, **kw)

        return wrapper

    return deco

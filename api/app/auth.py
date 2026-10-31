"""Session cookie auth for the SPA + CSRF double-submit + permission decorator."""

import secrets
from functools import wraps

from flask import current_app, g, jsonify, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .db import db
from .models import User
from .services.permissions import allowed

CSRF_COOKIE = "csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
#: Signed "this browser has signed in to <email> before" marker. It only lifts the
#: per-email failed-login cap (services/accounts.login); it grants no access.
KNOWN_DEVICE_COOKIE = "wn_known"
KNOWN_DEVICE_DAYS = 180


def csrf_ok() -> bool:
    """Double-submit check. A header with non-ASCII characters is simply wrong (403),
    not a TypeError from compare_digest (500)."""
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not (cookie.isascii() and header.isascii()):
        return False
    return secrets.compare_digest(cookie, header)


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
    session.clear()
    session["uid"] = user.id
    session["tok"] = user.session_token
    session.permanent = True
    g.u = user


def end_session() -> None:
    session.clear()
    g.u = None


def csrf_token() -> str:
    tok = request.cookies.get(CSRF_COOKIE)
    return tok if tok and len(tok) == 43 else secrets.token_urlsafe(32)


def set_csrf_cookie(resp, token: str):
    resp.set_cookie(
        CSRF_COOKIE,
        token,
        samesite="Lax",
        secure=request.is_secure or current_app.config.get("APP_ENV") == "production",
        httponly=False,
        max_age=30 * 86400,
    )
    return resp


def require(permission: str | None = None):
    """Route needs a logged-in user (and a permission). Also enforces CSRF on mutating requests."""

    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if user is None:
                return jsonify({"error": "not signed in"}), 401
            if request.method not in SAFE_METHODS and not csrf_ok():
                return jsonify({"error": "missing or invalid CSRF token"}), 403
            if permission and not allowed(user.role, permission):
                return jsonify(
                    {"error": "you do not have permission to do that", "permission": permission}
                ), 403
            return fn(*a, **kw)

        return wrapper

    return deco

"""Session cookie auth for the SPA + CSRF double-submit + permission decorator."""

import secrets
from functools import wraps

from flask import current_app, g, jsonify, request, session

from .db import db
from .models import User
from .services.permissions import allowed

CSRF_COOKIE = "csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


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
            if request.method not in SAFE_METHODS:
                cookie = request.cookies.get(CSRF_COOKIE)
                header = request.headers.get(CSRF_HEADER)
                if not cookie or not header or not secrets.compare_digest(cookie, header):
                    return jsonify({"error": "missing or invalid CSRF token"}), 403
            if permission and not allowed(user.role, permission):
                return jsonify(
                    {"error": "you do not have permission to do that", "permission": permission}
                ), 403
            return fn(*a, **kw)

        return wrapper

    return deco

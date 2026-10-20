from flask import Blueprint, current_app, g, jsonify, request

from .. import serializers
from ..auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SAFE_METHODS,
    csrf_token,
    current_user,
    end_session,
    require,
    set_csrf_cookie,
    start_session,
)
from ..models import Role
from ..services import accounts, mail
from ..services.accounts import AccountError

bp = Blueprint("auth", __name__)


def _dev_links_allowed() -> bool:
    return not mail.configured() and current_app.config.get("APP_ENV") != "production"


@bp.before_request
def csrf_on_writes():
    """Even unauthenticated writes (login, sign-up) carry the double-submit token."""
    if request.method in SAFE_METHODS:
        return None
    import secrets

    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        return jsonify({"error": "missing or invalid CSRF token"}), 403
    return None


@bp.errorhandler(AccountError)
def account_error(e: AccountError):
    return jsonify({"error": str(e)}), e.status


@bp.get("/csrf")
def csrf():
    token = csrf_token()
    return set_csrf_cookie(jsonify({"token": token}), token)


@bp.get("/me")
def me():
    user = current_user()
    if user is None:
        return jsonify({"error": "not signed in", "signupsOpen": accounts.signups_open()}), 401
    return jsonify(serializers.me(user))


@bp.post("/signup")
def signup():
    d = request.get_json(silent=True) or {}
    user = accounts.signup(
        d.get("organization", ""), d.get("name", ""), d.get("email", ""), d.get("password", "")
    )
    start_session(user)
    return jsonify(serializers.me(user)), 201


@bp.post("/login")
def login():
    d = request.get_json(silent=True) or {}
    user = accounts.login(d.get("email", ""), d.get("password", ""))
    start_session(user)
    return jsonify(serializers.me(user))


@bp.post("/logout")
@require()
def logout():
    end_session()
    return ("", 204)


@bp.post("/logout-everywhere")
@require()
def logout_everywhere():
    accounts.logout_everywhere(g.u)
    end_session()
    return ("", 204)


@bp.get("/invites/<token>")
def invite_info(token: str):
    inv = accounts.invite_by_token(token)
    return jsonify(
        {"email": inv.email, "role": inv.role.value, "organization": inv.organization.name}
    )


@bp.post("/invites/accept")
def accept_invite():
    d = request.get_json(silent=True) or {}
    user = accounts.accept_invite(d.get("token", ""), d.get("name", ""), d.get("password", ""))
    start_session(user)
    return jsonify(serializers.me(user)), 201


@bp.post("/password/forgot")
def forgot():
    d = request.get_json(silent=True) or {}
    token = accounts.request_password_reset(d.get("email", ""))
    body: dict = {"ok": True}
    if token and _dev_links_allowed():
        body["resetUrl"] = accounts.app_url("/reset/" + token)
    return jsonify(body), 202


@bp.post("/password/reset")
def reset():
    d = request.get_json(silent=True) or {}
    user = accounts.reset_password(d.get("token", ""), d.get("password", ""))
    start_session(user)
    return jsonify(serializers.me(user))


ROLES = [r.value for r in Role]

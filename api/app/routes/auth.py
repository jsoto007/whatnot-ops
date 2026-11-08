from flask import Blueprint, current_app, g, jsonify, request

from .. import serializers
from ..auth import (
    SAFE_METHODS,
    csrf_ok,
    csrf_token,
    current_user,
    end_session,
    known_device_tags,
    remember_device,
    require,
    start_session,
)
from ..config import is_dev
from ..models import Role
from ..services import accounts, mail, ratelimit
from ..services.accounts import AccountError

bp = Blueprint("auth", __name__)


def _dev_links_allowed() -> bool:
    return not mail.configured() and is_dev(current_app.config.get("APP_ENV"))


@bp.before_request
def csrf_on_writes():
    """Even unauthenticated writes (login, sign-up) carry the double-submit token."""
    if request.method in SAFE_METHODS or csrf_ok():
        return None
    return jsonify({"error": "missing or invalid CSRF token"}), 403


@bp.errorhandler(AccountError)
def account_error(e: AccountError):
    return jsonify({"error": str(e)}), e.status


@bp.get("/csrf")
def csrf():
    return jsonify({"token": csrf_token()})


@bp.get("/me")
def me():
    user = current_user()
    if user is None:
        open_, admin_only = accounts.signup_state()
        return jsonify(
            {
                "error": "not signed in",
                "signupsOpen": open_,
                "signupAdminOnly": admin_only,
                "setupCodeRequired": open_ and accounts.setup_code_required(),
            }
        ), 401
    return jsonify(serializers.me(user))


@bp.post("/signup")
def signup():
    ratelimit.per_ip("signup", 10, 3600)
    d = request.get_json(silent=True) or {}
    user = accounts.signup(
        d.get("organization", ""),
        d.get("name", ""),
        d.get("email", ""),
        d.get("password", ""),
        setup_code=d.get("setupCode"),
    )
    start_session(user)
    return remember_device(jsonify(serializers.me(user)), user), 201


@bp.post("/login")
def login():
    ratelimit.per_ip("login", 30, 600)
    d = request.get_json(silent=True) or {}
    user = accounts.login(
        d.get("email", ""),
        d.get("password", ""),
        ip=ratelimit.client_ip(),
        known_tags=known_device_tags(),
    )
    start_session(user)
    return remember_device(jsonify(serializers.me(user)), user)


@bp.post("/logout")
@require()
def logout():
    accounts.logout(g.u)
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
    ratelimit.per_ip("invite-info", 30, 600)
    inv = accounts.invite_by_token(token)
    return jsonify(
        {"email": inv.email, "role": inv.role.value, "organization": inv.organization.name}
    )


@bp.post("/invites/accept")
def accept_invite():
    ratelimit.per_ip("invite-accept", 20, 600)
    d = request.get_json(silent=True) or {}
    user = accounts.accept_invite(d.get("token", ""), d.get("name", ""), d.get("password", ""))
    start_session(user)
    return remember_device(jsonify(serializers.me(user)), user), 201


@bp.post("/password/forgot")
def forgot():
    ratelimit.per_ip("forgot", 10, 900)
    d = request.get_json(silent=True) or {}
    token = accounts.request_password_reset(d.get("email", ""), return_token=_dev_links_allowed())
    body: dict = {"ok": True}
    if token and _dev_links_allowed():
        body["resetUrl"] = accounts.app_url("/reset/" + token)
    return jsonify(body), 202


@bp.post("/password/reset")
def reset():
    ratelimit.per_ip("reset", 20, 600)
    d = request.get_json(silent=True) or {}
    user = accounts.reset_password(d.get("token", ""), d.get("password", ""))
    start_session(user)
    return remember_device(jsonify(serializers.me(user)), user)


ROLES = [r.value for r in Role]

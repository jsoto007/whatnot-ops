from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import Invite, Role, User
from ..services import accounts, mail
from ..services.accounts import AccountError
from ..services.permissions import invitable_roles

bp = Blueprint("team", __name__)


@bp.errorhandler(AccountError)
def account_error(e: AccountError):
    return jsonify({"error": str(e)}), e.status


def _role(value: str) -> Role:
    try:
        return Role(value)
    except ValueError:
        raise AccountError(f"unknown role '{value}'") from None


@bp.get("")
@require("team.view")
def list_team():
    org_id = g.u.organization_id
    users = db.session.scalars(
        select(User).where(User.organization_id == org_id).order_by(User.created_at)
    ).all()
    invites = db.session.scalars(
        select(Invite).where(Invite.organization_id == org_id).order_by(Invite.created_at.desc())
    ).all()
    return jsonify(
        {
            "users": [serializers.user(u) for u in users],
            "invites": [serializers.invite(i) for i in invites if i.pending],
            "invitableRoles": sorted(r.value for r in invitable_roles(g.u.role)),
        }
    )


@bp.post("/invites")
@require("team.invite")
def create_invite():
    d = request.get_json(silent=True) or {}
    invite, token = accounts.create_invite(g.u, d.get("email", ""), _role(d.get("role", "")))
    body = {"invite": serializers.invite(invite), "emailSent": mail.configured()}
    if not mail.configured() and current_app.config.get("APP_ENV") != "production":
        body["acceptUrl"] = accounts.app_url("/invite/" + token)
    return jsonify(body), 201


@bp.delete("/invites/<int:invite_id>")
@require("team.invite")
def revoke_invite(invite_id: int):
    accounts.revoke_invite(g.u, invite_id)
    return ("", 204)


@bp.patch("/users/<int:user_id>")
@require("team.manage")
def update_user(user_id: int):
    d = request.get_json(silent=True) or {}
    role = _role(d["role"]) if "role" in d else None
    disabled = bool(d["disabled"]) if "disabled" in d else None
    user = accounts.update_user(g.u, user_id, role=role, disabled=disabled)
    return jsonify(serializers.user(user))

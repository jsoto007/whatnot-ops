from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import select

from .. import serializers
from ..auth import require
from ..config import is_dev
from ..db import db
from ..models import Invite, Role, User
from ..services import accounts, mail, ratelimit, sale_mode, text
from ..services.accounts import AccountError
from ..services.permissions import invitable_roles

bp = Blueprint("team", __name__)
# Invites one person may send per hour. Each call mails a link (Mailgun cost, and the
# shop's name on the sender line), so a stolen session must not turn into a spam run;
# the general write limit (300/min) alone would allow thousands an hour.
INVITES_PER_HOUR = 20


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
    ratelimit.per_user("invite", g.u.id, INVITES_PER_HOUR, 3600)
    d = request.get_json(silent=True) or {}
    invite, token = accounts.create_invite(g.u, d.get("email", ""), _role(d.get("role", "")))
    body = {"invite": serializers.invite(invite), "emailSent": mail.configured()}
    if not mail.configured() and is_dev(current_app.config.get("APP_ENV")):
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


@bp.patch("/organization")
@require("settings.manage")
def update_organization():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        d = {}
    org = g.u.organization
    changes: dict = {}
    if "name" in d and (new_name := text.typed(d["name"], 120)):
        changes["name"] = [org.name, new_name]
        org.name = new_name
    if "hostUsername" in d:
        value = (d["hostUsername"] or "").strip().lstrip("@")[:80] or None
        changes["hostUsername"] = [org.host_username, value]
        org.host_username = value
    if "saleMode" in d:
        try:
            before, now = sale_mode.set_shop(org, d["saleMode"])
        except sale_mode.ModeError as e:
            db.session.rollback()
            return jsonify({"error": str(e)}), e.status
        if before != now:
            changes["saleMode"] = [before, now]
    if "autoPick" in d:
        if not isinstance(d["autoPick"], bool):
            return jsonify({"error": "Say yes or no."}), 400
        if org.auto_pick != d["autoPick"]:
            changes["autoPick"] = [org.auto_pick, d["autoPick"]]
            org.auto_pick = d["autoPick"]
    if changes:
        from ..services import audit

        audit.record(
            org.id,
            "organization.updated",
            user=g.u,
            target_type="organization",
            target_id=org.id,
            details=changes,
        )
        db.session.commit()
    return jsonify({"organization": serializers.organization(org)})

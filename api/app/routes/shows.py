from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select

from .. import serializers
from ..auth import require
from ..db import db
from ..models import ChatMessage, Show

bp = Blueprint("shows", __name__)


def _counts(show_ids: list[int]) -> dict[int, int]:
    if not show_ids:
        return {}
    rows = db.session.execute(
        select(ChatMessage.show_id, func.count())
        .where(ChatMessage.show_id.in_(show_ids))
        .group_by(ChatMessage.show_id)
    ).all()
    return {sid: n for sid, n in rows}


def _show_or_404(show_id: int) -> Show | None:
    show = db.session.get(Show, show_id)
    return show if show and show.organization_id == g.u.organization_id else None


@bp.get("")
@require("shows.view")
def list_shows():
    shows = db.session.scalars(
        select(Show)
        .where(Show.organization_id == g.u.organization_id)
        .order_by(Show.started_at.desc())
        .limit(200)
    ).all()
    counts = _counts([s.id for s in shows])
    return jsonify({"shows": [serializers.show(s, counts.get(s.id, 0)) for s in shows]})


@bp.get("/<int:show_id>")
@require("shows.view")
def get_show(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    return jsonify({"show": serializers.show(show, _counts([show.id]).get(show.id, 0))})


@bp.get("/<int:show_id>/messages")
@require("shows.view")
def messages(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", 200)), 1000)
    offset = max(int(request.args.get("offset", 0)), 0)
    stmt = select(ChatMessage).where(ChatMessage.show_id == show.id)
    if q:
        stmt = stmt.where(ChatMessage.raw_text.ilike(f"%{q}%"))
    total = db.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.session.scalars(stmt.order_by(ChatMessage.seq).offset(offset).limit(limit)).all()
    return jsonify(
        {
            "total": total,
            "messages": [
                {
                    "id": m.id,
                    "seq": m.seq,
                    "username": m.username,
                    "body": m.body,
                    "rawText": m.raw_text,
                    "capturedAt": m.captured_at.isoformat(),
                }
                for m in rows
            ],
        }
    )

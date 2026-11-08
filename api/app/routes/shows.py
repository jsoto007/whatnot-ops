from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .. import serializers
from ..auth import require
from ..db import db
from ..models import ChatMessage, EventKind, Job, ParsedEvent, ReviewItem, ReviewStatus, Sale, Show
from ..services import audit, pipeline, ratelimit, sale_chat

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


def _stats(show: Show) -> dict:
    events = dict(
        db.session.execute(
            select(ParsedEvent.kind, func.count())
            .where(ParsedEvent.show_id == show.id)
            .group_by(ParsedEvent.kind)
        ).all()
    )
    sales = dict(
        db.session.execute(
            select(Sale.status, func.count()).where(Sale.show_id == show.id).group_by(Sale.status)
        ).all()
    )
    open_reviews = db.session.scalar(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.show_id == show.id, ReviewItem.status == ReviewStatus.open)
    )
    job = db.session.scalar(
        select(Job)
        .where(Job.kind == "parse_show", Job.payload["show_id"].as_integer() == show.id)
        .order_by(Job.id.desc())
        .limit(1)  # a long show leaves one job per upload batch
    )
    return {
        "parsed": sum(events.values()),
        "events": {k.value: v for k, v in events.items()},
        "sales": {k.value: v for k, v in sales.items()},
        "openReviews": open_reviews or 0,
        # The job's own error (an exception line) stays in the worker log and the jobs table.
        "job": {"id": job.id, "status": job.status.value} if job else None,
    }


@bp.get("/<int:show_id>")
@require("shows.view")
def get_show(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    return jsonify(
        {"show": serializers.show(show, _counts([show.id]).get(show.id, 0)), "stats": _stats(show)}
    )


@bp.post("/<int:show_id>/parse")
@require("review.act")
def parse_now(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    # A full parse of a long show is heavy: a shop gets a few a minute.
    ratelimit.per_org("parse", g.u.organization_id, 20, 60)
    reparse = bool((request.get_json(silent=True) or {}).get("reparse"))
    try:
        stats = pipeline.process_show(show.id, reparse=reparse)
    except pipeline.ParseBusy:
        db.session.rollback()
        return jsonify(
            {"error": "This show is being parsed right now. Try again in a few seconds."}
        ), 409
    audit.record(
        g.u.organization_id,
        "show.parsed",
        user=g.u,
        target_type="show",
        target_id=show.id,
        details=vars(stats),
    )
    db.session.commit()
    return jsonify(_stats(show))


@bp.post("/<int:show_id>/ai-audit")
@require("review.act")
def ai_audit(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    from ..services import supervisor

    if not supervisor.status(g.u.organization_id)["enabled"]:
        return jsonify({"error": "AI checks are not turned on for this shop"}), 409
    # Each audit is a paid call to the AI vendor.
    ratelimit.per_org("ai-audit", g.u.organization_id, 30, 3600)
    supervisor.schedule_audit(show, delay_s=0)
    audit.record(
        g.u.organization_id,
        "show.ai_audit_requested",
        user=g.u,
        target_type="show",
        target_id=show.id,
    )
    db.session.commit()
    return jsonify(_stats(show))


@bp.get("/<int:show_id>/events")
@require("shows.view")
def events(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    kind = request.args.get("kind") or ""
    limit = max(1, min(request.args.get("limit", 200, type=int) or 200, 1000))
    if kind and kind not in {k.value for k in EventKind}:
        return jsonify({"error": f"unknown kind {kind!r}"}), 400
    q = (
        select(ParsedEvent)
        .where(ParsedEvent.show_id == show.id)
        .options(selectinload(ParsedEvent.message))
    )
    q = (
        q.where(ParsedEvent.kind == EventKind(kind))
        if kind
        else q.where(ParsedEvent.kind != EventKind.chatter)
    )
    total = db.session.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.session.scalars(
        q.join(ChatMessage, ChatMessage.id == ParsedEvent.message_id)
        .order_by(ChatMessage.seq)
        .limit(limit)
    ).all()
    return jsonify({"total": total, "events": [serializers.event(e) for e in rows]})


@bp.get("/<int:show_id>/sales")
@require("shows.view")
def sales(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    rows = db.session.scalars(
        select(Sale)
        .where(Sale.show_id == show.id)
        .options(
            selectinload(Sale.sku),
            selectinload(Sale.event).selectinload(ParsedEvent.message),
            selectinload(Sale.show),
        )
        .order_by(Sale.id)
    ).all()
    chat = sale_chat.around(rows)
    return jsonify({"sales": [serializers.sale(s, chat[s.id]) for s in rows]})


@bp.get("/<int:show_id>/messages")
@require("shows.view")
def messages(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    q = (request.args.get("q") or "").strip()[:200]
    if q:
        ratelimit.per_user("chat-search", g.u.id, 120, 60)
    limit = max(1, min(request.args.get("limit", 200, type=int) or 200, 1000))
    offset = max(request.args.get("offset", 0, type=int) or 0, 0)
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

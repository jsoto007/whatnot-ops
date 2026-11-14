from flask import Blueprint, g, jsonify, request
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import selectinload

from .. import serializers
from ..auth import require
from ..db import db
from ..models import (
    ChatMessage,
    EventKind,
    Job,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    Show,
    WhatnotShow,
)
from ..services import audit, jobs, picks, pipeline, ratelimit, sale_chat, sale_mode, shelves
from ..services.whatnot import WhatnotError, connection
from ..services.whatnot import orders as whatnot_orders
from ..services.whatnot import sync as whatnot_sync

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
        .options(selectinload(Show.whatnot_show))
        .order_by(Show.started_at.desc())
        .limit(200)
    ).all()
    counts = _counts([s.id for s in shows])
    modes = sale_mode.of_shows(list(shows))
    return jsonify(
        {"shows": [serializers.show(s, counts.get(s.id, 0), modes[s.id]) for s in shows]}
    )


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
        {
            "show": serializers.show(show, _counts([show.id]).get(show.id, 0)),
            "stats": _stats(show),
            # Laptops whose clock is out for this show: winners' picks are then not suggested.
            "clockProblems": sale_chat.clock_problems(show),
            # A winner's-pick show with no shelves laid out: no pick can be read from chat.
            "noShelves": (
                sale_mode.of_show(show)[0] == sale_mode.WINNERS_PICK and not shelves.spots(show)
            ),
        }
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


@bp.get("/<int:show_id>/shelves")
@require("shows.view")
def get_shelves(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    return jsonify({"shelves": shelves.read(show), "canCopy": shelves.can_copy(show)})


@bp.post("/<int:show_id>/shelves/copy")
@require("review.act")
def copy_shelves(show_id: int):
    """Lay the show's shelves out as the shop's last show had them."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    try:
        layout = shelves.copy_last(show)
    except shelves.ShelfError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), e.status
    audit.record(
        g.u.organization_id,
        "show.shelves",
        user=g.u,
        target_type="show",
        target_id=show.id,
        details={"spots": len(layout), "copied": True},
    )
    db.session.commit()
    return jsonify({"shelves": layout, "canCopy": False})


@bp.post("/<int:show_id>/shelves")
@require("review.act")
def save_shelves(show_id: int):
    """Lay out the show's shelves: `shelves` is [{spot: "B4", code: "<product code>"}]."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    given = (request.get_json(silent=True) or {}).get("shelves")
    if not isinstance(given, list) or not all(
        isinstance(r, dict) and isinstance(r.get("spot"), str) and isinstance(r.get("code"), str)
        for r in given
    ):
        return jsonify({"error": "Send the shelf spots as a list of spot and product code."}), 400
    try:
        layout = shelves.save(show, [(r["spot"], r["code"]) for r in given])
    except shelves.ShelfError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), e.status
    audit.record(
        g.u.organization_id,
        "show.shelves",
        user=g.u,
        target_type="show",
        target_id=show.id,
        details={"spots": len(layout)},
    )
    db.session.commit()
    return jsonify({"shelves": layout})


@bp.post("/<int:show_id>/mode")
@require("review.act")
def set_mode(show_id: int):
    """How this show sells: `mode` is "winners_pick" or "listing", or null to follow the
    shop's usual way again. Every recording of the same Whatnot show gets the same answer."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "mode" not in body:
        return jsonify({"error": "Say how this show is sold."}), 400
    try:
        before, now = sale_mode.set_show(show, body["mode"])
    except sale_mode.ModeError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), e.status
    audit.record(
        g.u.organization_id,
        "show.mode",
        user=g.u,
        target_type="show",
        target_id=show.id,
        details={"mode": now, "was": before, "own": body["mode"] is not None},
    )
    db.session.commit()
    return jsonify({"show": serializers.show(show, _counts([show.id]).get(show.id, 0))})


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


# ---------- Whatnot's own record of the show ----------

WHATNOT_REFRESH_PER_10_MIN = 10  # each one pages through Whatnot's orders


@bp.errorhandler(WhatnotError)
def whatnot_error(e: WhatnotError):
    db.session.rollback()
    return jsonify({"error": e.words}), e.http_status


@bp.errorhandler(whatnot_orders.OrdersError)
def whatnot_orders_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


def _whatnot_view(show: Show) -> dict:
    org_id = show.organization_id
    conn = connection.active(org_id)
    if conn is None:
        return {"connected": False}
    ws = whatnot_orders.whatnot_show_of(show)
    day = show.started_at
    choices = db.session.scalars(
        select(WhatnotShow)
        .where(
            WhatnotShow.organization_id == org_id,
            WhatnotShow.status.in_(("live", "stopped", "ended")),
        )
        .order_by(
            func.abs(
                func.extract(
                    "epoch",
                    func.coalesce(WhatnotShow.started_at, WhatnotShow.scheduled_start_at) - day,
                )
            ).nulls_last()
        )
        .limit(12)
    ).all()
    out = {
        "connected": True,
        "mode": conn.orders_mode,
        "link": show.whatnot_link,
        "show": serializers.whatnot_show(ws) if ws is not None else None,
        "choices": [serializers.whatnot_show(c) for c in choices],
        "complete": False,
        "info": None,
        "summary": {},
        "rows": [],
        "uncovered": [],
    }
    if ws is None:
        return out
    result = whatnot_orders.compare(show)
    plan = result["plan"]
    uncovered = (
        db.session.scalars(
            select(Sale)
            .where(Sale.id.in_(plan.uncovered))
            .options(selectinload(Sale.sku), selectinload(Sale.show))
            .order_by(Sale.id)
        ).all()
        if plan.uncovered
        else []
    )
    out.update(
        complete=result["complete"],
        info=result["info"],
        summary=plan.summary,
        rows=[serializers.reconcile_row(r) for r in plan.rows],
        uncovered=[
            {
                "saleId": s.id,
                "buyer": s.buyer_username,
                "item": s.item_text or (s.sku.code if s.sku else ""),
                "quantity": s.quantity,
                "priceCents": s.price_cents,
                "status": s.status.value,
            }
            for s in uncovered
        ],
    )
    return out


@bp.get("/<int:show_id>/whatnot")
@require("orders.view", heavy=True)
def whatnot_compare(show_id: int):
    """Whatnot's orders for the show next to the app's sales: what applying them would do."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    out = _whatnot_view(show)
    db.session.rollback()  # a comparison never writes
    return jsonify(out)


@bp.post("/<int:show_id>/whatnot/link")
@require("orders.reconcile")
def whatnot_link(show_id: int):
    """Say by hand which Whatnot show this recording is of (or that it is of none)."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    if show.whatnot_link == "made":
        return jsonify({"error": "this show was made from the Whatnot show itself"}), 409
    target = (request.get_json(silent=True) or {}).get("whatnotShowId")
    ws = None
    if target is not None:
        ws = db.session.get(WhatnotShow, target) if isinstance(target, int) else None
        if ws is None or ws.organization_id != g.u.organization_id:
            return jsonify({"error": "that Whatnot show was not found"}), 404
    # A link is not chat arriving: the show's "last update" stays as it was.
    whatnot_sync.tie(show, ws.id if ws is not None else None, "manual")
    audit.record(
        g.u.organization_id,
        "whatnot.show_linked",
        user=g.u,
        target_type="show",
        target_id=show.id,
        details={"title": ws.title if ws is not None else None},
    )
    db.session.commit()
    return jsonify(_whatnot_view(show))


@bp.post("/<int:show_id>/whatnot/refresh")
@require("orders.reconcile")
def whatnot_refresh(show_id: int):
    """Read the show's orders from Whatnot again, now."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    ws = whatnot_orders.whatnot_show_of(show)
    if ws is None:
        return jsonify({"error": "This show is not matched to a show on Whatnot yet."}), 409
    ratelimit.per_org("whatnot-refresh", g.u.organization_id, WHATNOT_REFRESH_PER_10_MIN, 600)
    job = jobs.enqueue(
        "whatnot_sync_show",
        {"org_id": show.organization_id, "whatnot_show_id": ws.whatnot_id},
        org_id=show.organization_id,
        dedupe_key=f"whatnot_sync_show:{show.organization_id}:{ws.whatnot_id}",
    )
    db.session.commit()
    job = db.session.get(Job, job.id)
    if job is not None and job.error:  # ran at once (no worker) and Whatnot refused or failed
        return jsonify({"error": "Whatnot could not be read just now. Try again in a minute."}), 502
    queued = job is not None and job.status.value != "done"
    return jsonify({**_whatnot_view(_show_or_404(show_id)), "queued": queued})


@bp.post("/<int:show_id>/whatnot/apply")
@require("orders.reconcile")
def whatnot_apply(show_id: int):
    """Apply Whatnot's orders to the show's sales: payments, cancellations, and a sale for
    each paid order the app has none for."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    ratelimit.per_user("reconcile", g.u.id, 30, 600)
    try:
        result = whatnot_orders.apply(show, user=g.u)
    except OperationalError as e:
        # Two transactions each waiting on a buyer the other holds (this, and another
        # show being read): Postgres stops one. Nothing was changed; say so and let the
        # person press again.
        db.session.rollback()
        if "deadlock" not in str(getattr(e, "orig", e)).lower():
            raise
        return jsonify(
            {"error": "The show is being read right now. Try again in a few seconds."}
        ), 409
    audit.record(
        g.u.organization_id,
        "whatnot.orders_applied",
        user=g.u,
        target_type="show",
        target_id=show.id,
        details=result["summary"],
    )
    db.session.commit()
    show = _show_or_404(show_id)
    # A show that is over: its winners' picks are read once they close (services/picks.py).
    picks.schedule(whatnot_orders.whatnot_show_of(show))
    db.session.commit()
    return jsonify({**_whatnot_view(show), "applied": result["summary"]})

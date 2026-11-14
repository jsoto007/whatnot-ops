"""Orders → Shipping: one package and one label per buyer for a show, from Whatnot."""

from flask import Blueprint, Response, g, jsonify, request
from sqlalchemy import select

from ..auth import require
from ..db import db
from ..models import Show, WhatnotShipment
from ..services import audit, ratelimit
from ..services.orders import OrderError
from ..services.whatnot import WhatnotError, shipping
from ..services.whatnot import orders as whatnot_orders

bp = Blueprint("shipping", __name__)

REFRESH_PER_10_MIN = 10
LABEL_REQUESTS_PER_10_MIN = 20
PRINTS_PER_MIN = 120  # one buyer after another, at the packing table
# "Print labels" for a show: each press fetches up to shipping.LABELS_PER_PRINT labels.
BATCH_PRINTS_PER_MIN = 12


@bp.errorhandler(WhatnotError)
def whatnot_error(e: WhatnotError):
    db.session.rollback()
    return jsonify({"error": e.words}), e.http_status


@bp.errorhandler(shipping.ShippingError)
@bp.errorhandler(OrderError)
def shipping_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


def _show_or_404(show_id: int) -> Show | None:
    show = db.session.get(Show, show_id)
    return show if show and show.organization_id == g.u.organization_id else None


@bp.get("/shows/<int:show_id>")
@require("orders.view", heavy=True)
def show_packages(show_id: int):
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    return jsonify(
        {"showId": show.id, "showDate": show.show_date.isoformat(), **shipping.packages(show)}
    )


@bp.post("/shows/<int:show_id>/refresh")
@require("orders.fulfill")
def refresh(show_id: int):
    """Read the show's shipments from Whatnot again, now."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    ws = whatnot_orders.whatnot_show_of(show)
    if ws is None:
        return jsonify({"error": "This show is not matched to a show on Whatnot yet."}), 409
    ratelimit.per_org("whatnot-refresh", g.u.organization_id, REFRESH_PER_10_MIN, 600)
    # The show's orders first (a last win may not be here yet), then the parcels they are
    # in: the orders job queues the shipments read itself.
    from ..models import Job
    from ..services import jobs

    job = jobs.enqueue(
        "whatnot_sync_show",
        {"org_id": show.organization_id, "whatnot_show_id": ws.whatnot_id},
        org_id=show.organization_id,
        dedupe_key=f"whatnot_sync_show:{show.organization_id}:{ws.whatnot_id}",
    )
    db.session.commit()
    job_id = job.id
    db.session.expire_all()
    job = db.session.get(Job, job_id)
    if job is not None and job.error:
        return jsonify({"error": "Whatnot could not be read just now. Try again in a minute."}), 502
    waiting = db.session.scalar(
        select(Job.id)
        .where(
            Job.organization_id == g.u.organization_id,
            Job.kind.in_(("whatnot_sync_show", "whatnot_sync_shipments")),
            Job.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    show = _show_or_404(show_id)
    return jsonify({"showId": show.id, **shipping.packages(show), "queued": waiting is not None})


@bp.post("/shows/<int:show_id>/labels")
@require("orders.labels")
def request_labels(show_id: int):
    """Ask Whatnot to make the labels of the buyers who are ready (or of the ones named)."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    ratelimit.per_user("labels", g.u.id, LABEL_REQUESTS_PER_10_MIN, 600)
    d = request.get_json(silent=True) or {}
    buyers = d.get("buyers")
    if buyers is not None and not (
        isinstance(buyers, list) and all(isinstance(b, str) for b in buyers) and len(buyers) <= 500
    ):
        return jsonify({"error": "buyers must be a list of usernames"}), 400
    if d.get("confirm") is not True:
        # The page asks first: a label closes the buyer's bundle and cannot be undone.
        return jsonify({"error": "confirm that the labels should be made"}), 400
    result = shipping.request_labels(show, user=g.u, buyers=buyers)
    if result["requested"]:
        audit.record(
            g.u.organization_id,
            "labels.requested",
            user=g.u,
            target_type="show",
            target_id=show.id,
            details={"packages": result["requested"]},
        )
    db.session.commit()
    show = _show_or_404(show_id)
    return jsonify({"showId": show.id, **shipping.packages(show), **result})


@bp.post("/shows/<int:show_id>/mark-shipped")
@require("orders.fulfill")
def mark_shipped(show_id: int):
    """Move to Shipped the orders whose packages the carrier already has."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    n = shipping.mark_shipped(show, actor_label=g.u.email)
    if n:
        audit.record(
            g.u.organization_id,
            "whatnot.orders_shipped",
            user=g.u,
            target_type="show",
            target_id=show.id,
            details={"orders": n},
        )
    db.session.commit()
    show = _show_or_404(show_id)
    return jsonify({"showId": show.id, **shipping.packages(show), "marked": n})


@bp.post("/shows/<int:show_id>/labels/print")
@require("orders.fulfill", heavy=True)
def print_labels(show_id: int):
    """The show's next labels that have not been printed, as one PDF in the order of the order
    slips. Fetched from Whatnot now and joined in memory: nothing of them is kept, only the
    time each was printed. `slips: false` leaves Whatnot's packing slips out.

    The counts ride in headers (the body is the PDF): how many are in it, how many Whatnot
    did not hand over, and how many are still to print."""
    show = _show_or_404(show_id)
    if show is None:
        return jsonify({"error": "show not found"}), 404
    body = request.get_json(silent=True)
    slips = body.get("slips", True) if isinstance(body, dict) else True
    if not isinstance(slips, bool):
        return jsonify({"error": "slips must be true or false"}), 400
    ratelimit.per_user("labels-print", g.u.id, BATCH_PRINTS_PER_MIN, 60)
    out = shipping.print_labels(show, slip=slips)
    audit.record(
        g.u.organization_id,
        "labels.printed",
        user=g.u,
        target_type="show",
        target_id=show.id,
        details={"printed": out["printed"], "failed": out["failed"], "left": out["left"]},
    )
    db.session.commit()
    return Response(
        out["pdf"],
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="labels-show-{show.id}.pdf"',
            "X-Content-Type-Options": "nosniff",
            "X-Labels-Printed": str(out["printed"]),
            "X-Labels-Failed": str(out["failed"]),
            "X-Labels-Left": str(out["left"]),
        },
    )


@bp.get("/shipments/<int:shipment_id>/label.pdf")
@require("orders.fulfill")
def label(shipment_id: int):
    """The package's label (and Whatnot's packing slip), fetched from Whatnot now and
    passed straight through. It shows the buyer's address: nothing of it is kept here."""
    row = db.session.get(WhatnotShipment, shipment_id)
    if row is None or row.organization_id != g.u.organization_id:
        return jsonify({"error": "package not found"}), 404
    ratelimit.per_user("label-print", g.u.id, PRINTS_PER_MIN, 60)
    pdf, _ = shipping.label_pdf(row)
    shipping.mark_printed(row.organization_id, [row.id])  # "Print labels" skips it from now on
    audit.record(
        g.u.organization_id,
        "label.printed",
        user=g.u,
        target_type="shipment",
        target_id=row.id,
    )
    db.session.commit()
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="label-{row.id}.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )

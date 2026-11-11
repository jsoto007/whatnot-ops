"""Development and browser tests only: put shows and orders into the in-memory Whatnot.

Registered only when WHATNOT_API=fake, which the configuration refuses outside development
and test. It changes nothing but that in-memory fake (never a database row), so it needs no
sign-in: the browser tests call it the way they call the extension's upload address.
"""

from datetime import UTC, datetime, timedelta

from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("whatnot_fake", __name__)


@bp.post("")
def seed():
    fake = current_app.extensions["whatnot_fake"]
    d = request.get_json(silent=True) or {}
    if isinstance(d.get("seller"), dict):
        fake.seller.update({k: str(v) for k, v in d["seller"].items() if k in ("id", "username")})
    out: dict = {"shows": [], "orders": [], "shipments": []}
    now = datetime.now(UTC)
    for spec in d.get("shows") or []:
        show = fake.add_show(
            title=spec.get("title", "Show"),
            status=spec.get("status", "ended"),
            start=now - timedelta(minutes=spec.get("startedMinutesAgo", 120)),
            ended=now - timedelta(minutes=spec["endedMinutesAgo"])
            if "endedMinutesAgo" in spec
            else None,
        )
        out["shows"].append(show["id"])
    for spec in d.get("orders") or []:
        order = fake.add_order(
            show_id=out["shows"][spec.get("show", 0)] if out["shows"] else spec.get("showId"),
            buyer=spec["buyer"],
            items=[
                {
                    "title": i["title"],
                    "price_cents": i.get("priceCents", 100),
                    "quantity": i.get("quantity", 1),
                }
                for i in spec.get("items", [])
            ],
            status=spec.get("status", "pending_fulfillment"),
            transaction_type=spec.get("type", "auction"),
            created=now - timedelta(minutes=spec.get("minutesAgo", 60)),
        )
        out["orders"].append(order["id"])
    for spec in d.get("shipments") or []:
        shipment = fake.add_shipment(
            recipient=spec["recipient"],
            order_ids=[out["orders"][i] for i in spec.get("orders", [])],
            status=spec.get("status", "created"),
            tracking=spec.get("tracking"),
            required_actions=spec.get("requiredActions"),
        )
        out["shipments"].append(shipment["id"])
    for change in d.get("setOrders") or []:
        fake.set_order_status(str(change["id"]), change["status"])
    if d.get("finishLabels"):  # what Whatnot's worker does after a label was asked for
        for shipment_id in list(fake.labels_requested):
            if fake.shipments[shipment_id]["status"] == "created":
                fake.finish_label(shipment_id)
    for change in d.get("setShipments") or []:
        fake.set_shipment(str(change["id"]), status=change["status"])
    for spec in d.get("results") or []:  # how Whatnot says a show did
        show = fake.shows[spec["showId"]]
        fake.analytics[show["id"]] = {
            "show_id": show["id"],
            "title": show["title"],
            "start_time": show["scheduled_start_at"],
            "calculation_status": "GENERATED",
            **spec.get("data", {}),
        }
    if d.get("finishReports") is not None:  # Whatnot finishes building each report asked for
        from ..services.whatnot.fake import ledger_csv

        for report_id, report in fake.reports.items():
            if report["status"] == "pending":
                fake.finish_report(report_id, ledger_csv(d["finishReports"]))
    out["labelsRequested"] = list(fake.labels_requested)
    return jsonify(out), 201

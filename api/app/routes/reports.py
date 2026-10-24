from datetime import date

from flask import Blueprint, Response, g, jsonify, request

from ..auth import require
from ..db import db
from ..models import Show
from ..services import audit, dashboard, reports

bp = Blueprint("reports", __name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


@bp.get("/dashboard")
@require("reports.view")
def get_dashboard():
    return jsonify(dashboard.overview(g.u.organization_id))


@bp.get("/dashboard/counts")
@require("shows.view")
def get_counts():
    return jsonify(dashboard.counts(g.u.organization_id))


@bp.get("/dashboard/shows/<int:show_id>")
@require("shows.view")
def get_show_dashboard(show_id: int):
    show = db.session.get(Show, show_id)
    if show is None or show.organization_id != g.u.organization_id:
        return jsonify({"error": "show not found"}), 404
    return jsonify(
        {
            "tiles": dashboard.show_tiles(show),
            "topBuyers": dashboard.top_buyers(g.u.organization_id, show_id=show.id),
            "topSkus": dashboard.top_skus(g.u.organization_id, show_id=show.id),
            "winnersWithoutPurchase": dashboard.winners_without_purchase(show),
        }
    )


@bp.get("/reports/show/<int:show_id>.xlsx")
@require("reports.view")
def show_report(show_id: int):
    show = db.session.get(Show, show_id)
    if show is None or show.organization_id != g.u.organization_id:
        return jsonify({"error": "show not found"}), 404
    data = reports.build(g.u.organization, show=show)
    audit.record(
        g.u.organization_id, "report.downloaded", user=g.u, target_type="show", target_id=show.id
    )
    db.session.commit()
    name = f"whatnot_{show.show_date.isoformat()}_show.xlsx"
    return Response(
        data, mimetype=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@bp.get("/reports/overview.xlsx")
@require("reports.view")
def overview_report():
    try:
        since, until = _date(request.args.get("from")), _date(request.args.get("to"))
    except ValueError:
        return jsonify({"error": "dates must be YYYY-MM-DD"}), 400
    data = reports.build(g.u.organization, since=since, until=until)
    audit.record(
        g.u.organization_id,
        "report.downloaded",
        user=g.u,
        target_type="organization",
        target_id=g.u.organization_id,
        details={"from": str(since), "to": str(until)},
    )
    db.session.commit()
    name = f"whatnot_overview_{since or 'all'}_{until or 'now'}.xlsx"
    return Response(
        data, mimetype=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )

from datetime import UTC, date, datetime, timedelta

from flask import Blueprint, Response, g, jsonify, request
from sqlalchemy import select

from ..auth import require
from ..db import db
from ..models import Show
from ..services import audit, dashboard, ratelimit, reports, showdate
from ..services.permissions import allowed

bp = Blueprint("reports", __name__)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


@bp.get("/dashboard")
@require(
    "shows.view", heavy=True
)  # the landing page for every role; it shows nothing the show pages do not
def get_dashboard():
    return jsonify(dashboard.overview(g.u.organization_id))


@bp.get("/dashboard/counts")
@require("shows.view")
def get_counts():
    return jsonify(dashboard.counts(g.u.organization_id))


@bp.get("/dashboard/shows/<int:show_id>")
@require("shows.view", heavy=True)
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
            # Whatnot's own results and ledger for the show: money, so for those who may
            # read reports (not the packing role).
            "whatnot": dashboard.whatnot_show(show) if allowed(g.u.role, "reports.view") else None,
        }
    )


REPORTS_PER_10_MIN = 30
# The overview workbook is built in the web request, in memory: every sale of every show in
# the range. Measured 2026-11-09 on 30 shows of history: 14 s and a 518 MB peak, on a 512 MB
# instance. So a request with no dates means "the last 30 days", and no request covers more
# than MAX_OVERVIEW_SHOWS shows (about 4 s and 270 MB at 10 shows in the same measurement).
OVERVIEW_DEFAULT_DAYS = 30
MAX_OVERVIEW_SHOWS = 12


def _overview_range(org_id: int, since: date | None, until: date | None):
    """The range the overview covers, or an error in the shop's words. With no dates at
    all: the last 30 days, narrowed to the newest shows that fit one workbook."""
    asked = since is not None or until is not None
    if not asked:
        today = showdate.show_date(datetime.now(UTC))
        since = today - timedelta(days=OVERVIEW_DEFAULT_DAYS)
    q = select(Show.show_date).where(Show.organization_id == org_id)
    if since:
        q = q.where(Show.show_date >= since)
    if until:
        q = q.where(Show.show_date <= until)
    dates = db.session.scalars(q.order_by(Show.show_date.desc(), Show.id.desc())).all()
    if len(dates) > MAX_OVERVIEW_SHOWS:
        if asked:
            return (
                None,
                None,
                (
                    f"Those dates cover {len(dates)} shows. One report holds up to "
                    f"{MAX_OVERVIEW_SHOWS} shows: pick a shorter range."
                ),
            )
        since = dates[MAX_OVERVIEW_SHOWS - 1]
    return since, until, None


@bp.get("/reports/show/<int:show_id>.xlsx")
@require("reports.view")
def show_report(show_id: int):
    ratelimit.per_user("report", g.u.id, REPORTS_PER_10_MIN, 600)  # built synchronously
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
    ratelimit.per_user("report", g.u.id, REPORTS_PER_10_MIN, 600)
    try:
        since, until = _date(request.args.get("from")), _date(request.args.get("to"))
    except ValueError:
        return jsonify({"error": "dates must be YYYY-MM-DD"}), 400
    since, until, problem = _overview_range(g.u.organization_id, since, until)
    if problem:
        return jsonify({"error": problem}), 400
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

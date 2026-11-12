"""Whatnot's own numbers: a show's results and the financial ledger.

Against the in-memory fake. The ledger file's columns are the ones Whatnot documents; no
real report has been downloaded, so what an ORDER_EARNINGS amount contains (before or
after its fees) is not something these tests can say. That is why the app shows Whatnot's
amounts and fees side by side and adds up only Whatnot's own signed lines.
"""

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import (
    Show,
    WhatnotConnection,
    WhatnotLedgerEntry,
    WhatnotReport,
    WhatnotShow,
    WhatnotSyncState,
)
from app.services import dashboard, jobs
from app.services.whatnot import client_for, results
from app.services.whatnot.fake import ledger_csv, money
from tests import test_whatnot_orders as wo
from tests.test_whatnot_orders import read

shop = wo.shop  # the connected shop, its show matched to an ended show on Whatnot

GENERATED = {
    "calculation_status": "GENERATED",
    "sales": money(123456),
    "estimated_earnings": money(101010),
    "orders_count": 226,
    "average_order_value": money(546),
    "giveaway_orders_count": 21,
    "giveaway_spend": money(-11025),
    "show_impression_count": 48211,
    "show_clickthrough_rate": 7.126,
    "buyers_count": 73,
    "first_time_buyers_count": 19,
    "returning_buyers_count": 54,
    "giveaway_winners_count": 20,
    "buyer_shares_count": 31,
    "stream_minutes": 184,
    "max_concurrent_viewers": 412,
    "total_views": 9120,
    "distinct_viewers_count": 3877,
    "average_rating": 4.94,
}


def ws_of(shop):
    return db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"]))


def set_results(shop, data):
    show = shop["fake"].shows[shop["wshow"]]
    shop["fake"].analytics[shop["wshow"]] = {
        "show_id": show["id"],
        "title": show["title"],
        "start_time": show["scheduled_start_at"],
        **data,
    }


# ---------- a show's results ----------


def test_a_shows_results_are_kept_as_whatnot_states_them_with_money_in_cents(app, shop):
    read(app, shop, orders=False)
    set_results(shop, GENERATED)
    with app.app_context():
        ws = ws_of(shop)
        assert results.read_results(shop["org"], client_for(shop["org"]), ws) == "GENERATED"
        db.session.commit()
        got = dashboard.whatnot_show(db.session.get(Show, shop["show"]))
    assert got["resultsStatus"] == "GENERATED" and got["title"] == "Friday night"
    r = got["results"]
    assert (r["salesCents"], r["estimatedEarningsCents"], r["averageOrderValueCents"]) == (
        123456,
        101010,
        546,
    )
    assert r["giveawaySpendCents"] == -11025  # Whatnot signs it; the app does not flip it
    assert (r["ordersCount"], r["buyersCount"], r["firstTimeBuyersCount"]) == (226, 73, 19)
    assert (r["maxConcurrentViewers"], r["distinctViewersCount"], r["streamMinutes"]) == (
        412,
        3877,
        184,
    )
    assert (r["showClickthroughRate"], r["averageRating"]) == (7.13, 4.94)
    assert got["ledger"] is None  # no ledger line read yet


def test_results_that_are_not_ready_or_not_numbers_are_left_out_not_guessed(app, shop):
    read(app, shop, orders=False)
    with app.app_context():
        ws = ws_of(shop)
        client = client_for(shop["org"])
        assert results.read_results(shop["org"], client, ws) == "PENDING"  # the fake's default
        assert ws.analytics is None and ws.analytics_at is not None
        odd = {
            "calculation_status": "GENERATED",
            "orders_count": -4,
            "buyers_count": True,
            "total_views": "9120",
            "stream_minutes": 5_000_000_000,
            "sales": {"amount": "1e30", "currency": "USD"},
            "average_rating": "five",
            "something_new": 12,
            "max_concurrent_viewers": 9,
        }
        assert results.store_results(ws, odd) == "GENERATED"
        assert ws.analytics == {"max_concurrent_viewers": 9}
        assert results.store_results(ws, {"calculation_status": "WILL_NOT_BE_CALCULATED"}) == (
            "WILL_NOT_BE_CALCULATED"
        )
        assert ws.analytics is None


def test_results_are_asked_for_when_a_show_is_read_and_again_until_whatnot_has_them(
    app, shop, monkeypatch
):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    read(app, shop, orders=False)
    with app.app_context():
        results.schedule_results(shop["org"], shop["wshow"])
        db.session.commit()
        assert jobs.run_once("t").status.value == "done"
        assert ws_of(shop).analytics_status == "PENDING"
        again = jobs.claim("t")  # not ready: it queued itself to ask again in ten minutes
        assert again is None
        from app.models import Job

        waiting = db.session.scalar(select(Job).where(Job.status == "queued"))
        assert waiting.kind == "whatnot_show_results" and waiting.payload["tries"] == 1
        assert waiting.run_at > datetime.now(UTC) + timedelta(minutes=9)
        # Whatnot finishes; the queued job reads them and does not come back.
        set_results(shop, GENERATED)
        waiting.run_at = datetime.now(UTC)
        db.session.commit()
        assert jobs.run_once("t").status.value == "done"
        assert ws_of(shop).analytics_status == "GENERATED"
        assert (
            db.session.scalar(select(func.count()).select_from(Job).where(Job.status == "queued"))
            == 0
        )
        # The timer only asks about recent shows that are not final yet.
        db.session.query(WhatnotSyncState).update(
            {"last_started_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        db.session.commit()
        assert results.results_due(shop["org"]) == []
        ws_of(shop).analytics_status = "PENDING"
        db.session.commit()
        db.session.query(WhatnotSyncState).update(
            {"last_started_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        db.session.commit()
        assert results.results_due(shop["org"]) == [shop["wshow"]]


def test_a_token_that_may_not_read_reports_does_not_raise_the_connection_banner(app, shop):
    """Results and the ledger are extras: without the permission the rest keeps working,
    and only their own pages say so."""
    read(app, shop, orders=False)
    shop["fake"].scopes = {"read:orders", "read:shows", "read:shipments", "read:customers"}
    with app.app_context():
        results.show_results_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
        results.ledger_job({"org_id": shop["org"]})
        db.session.commit()
        assert db.session.scalar(select(WhatnotConnection)).problem is None
        status = results.status(shop["org"])
        assert (status["notAllowed"], status["lines"], status["building"]) == (True, 0, False)


# ---------- the ledger ----------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12.5", 1250),
        ("-3.10", -310),
        ("1,234.00", 123400),
        ("$7", 700),
        ("19.99", 1999),
        ("0.005", 1),  # half a cent rounds up, never through a float
        ("-0.005", -1),
        ("0.004", 0),
        ("0", 0),
        ("", None),
        ("  ", None),
        ("abc", None),
        ("1e30", None),
        ("NaN", None),
        ("(21.97)", -2197),  # an accountant's negative
        ("\u221221.97", -2197),  # a typeset minus sign
        ("-$5.00", -500),
        ("+5", 500),
        ("1,234,567.89", 123456789),
        ("12,50", None),  # not a thousands separator: never guessed (it would be 100x off)
        ("1.234,56", None),
        ("1,2,3", None),
        ("5.00 USD", None),
        ("--5", None),
        (".", None),
        ("99999999.99", None),  # more than a column holds: unread, not a crash
        (None, None),
    ],
)
def test_report_amounts_become_signed_integer_cents(text, expected):
    assert results.to_cents(text) == expected


def earnings(txn, order, show, amount, price, commission, processing, **kw):
    """One order's earnings line, as the financial report lists it."""
    return {
        "transaction_type": "ORDER_EARNINGS",
        "ledger_transaction_id": txn,
        "order_id": order,
        "livestream_id": show,
        "transaction_completed_at_utc": "2026-11-08T03:10:00Z",
        "order_placed_at_utc": "2026-11-08T03:00:00Z",
        "transaction_amount": amount,
        "original_item_price": price,
        "post_coupon_price": price,
        "buyer_paid": str(float(price) + 5.25),
        "commission_fee": commission,
        "processing_fee": processing,
        "quantity_sold": "1",
        "buy_format": "auction",
        "listing_title": "CHOOSE ANY PRODUCT YOU WANT #73",
        "buyer_name": "lani808",
        "buyer_state": "CA",
        "buyer_country": "US",
        **kw,
    }


def line(kind, txn, show, amount, at="2026-11-08T03:20:00Z", **kw):
    """Any other ledger line: a charge, a refund, a tip."""
    return {
        "transaction_type": kind,
        "ledger_transaction_id": txn,
        "livestream_id": show,
        "transaction_amount": amount,
        "transaction_completed_at_utc": at,
        **kw,
    }


def report(app, shop, rows):
    """Ask for the ledger, let Whatnot "build" the file, and import it."""
    with app.app_context():
        client = client_for(shop["org"])
        run = results.request(shop["org"], client)
        db.session.commit()
        assert results.check(shop["org"], client)["waiting"] == 1  # still being built
        shop["fake"].finish_report(run.whatnot_id, ledger_csv(rows))
        out = results.check(shop["org"], client)
        db.session.commit()
        return out


def test_the_ledger_is_imported_and_a_shows_net_is_the_sum_of_whatnots_own_lines(app, shop):
    """Worked example, in cents. One show:
      order A  item $25.00, commission $2.00, processing $1.03 -> ledger +$21.97
      order B  item $17.00, commission $1.36, processing $0.79 -> ledger +$14.85
      shipping charged to the seller                           -> ledger  -$5.25
      order A refunded                                         -> ledger -$21.97
      a tip                                                    -> ledger  +$3.00
    Net = 2197 + 1485 - 525 - 2197 + 300 = 1260 cents. A line of another show stays out."""
    read(app, shop, orders=False)
    show = shop["wshow"]
    rows = [
        earnings("t-1", "1001", show, "21.97", "25.00", "2.00", "1.03"),
        earnings("t-2", "1002", show, "14.85", "17.00", "1.36", "0.79"),
        line("SHIPPING_CHARGE", "t-3", show, "-5.25"),
        line("ORDER_REFUND", "t-4", show, "-21.97", "2026-11-09T10:00:00Z", order_id="1001"),
        line("TIP", "t-5", show, "3.00", "2026-11-08T03:30:00Z", buyer_name="kat.c"),
        earnings("t-6", "2001", "another-show", "50.00", "60.00", "6.00", "4.00"),
    ]
    assert report(app, shop, rows) == {"imported": 1, "new": 6, "waiting": 0}
    with app.app_context():
        got = results.figures(shop["org"], whatnot_show_id=show)
        assert (got["netCents"], got["currency"], got["otherCurrencyLines"]) == (1260, "USD", 0)
        assert {k["kind"]: (k["lines"], k["cents"]) for k in got["kinds"]} == {
            "ORDER_EARNINGS": (2, 3682),
            "TIP": (1, 300),
            "SHIPPING_CHARGE": (1, -525),
            "ORDER_REFUND": (1, -2197),
        }
        assert got["fees"] == {
            "commission": 336,
            "commission_tax": 0,
            "processing_fee": 182,
            "processing_fee_tax": 0,
            "seller_shipping": 0,
            "coupon": 0,
        }
        assert (got["itemPricesCents"], got["costOfGoodsCents"]) == (4200, None)
        assert got["through"] == "2026-11-09T10:00:00+00:00"
        # Of the buyer only the username is kept: no state, no country, anywhere.
        row = db.session.scalar(
            select(WhatnotLedgerEntry).where(WhatnotLedgerEntry.ledger_transaction_id == "t-1")
        )
        kept = {c.name: getattr(row, c.name) for c in WhatnotLedgerEntry.__table__.columns}
        assert (kept["buyer_username"], kept["amount_cents"], kept["quantity"]) == (
            "lani808",
            2197,
            1,
        )
        assert "CA" not in map(str, kept.values())
        assert not any("state" in name or "country" in name for name in kept)
        run = db.session.scalar(select(WhatnotReport))
        assert (run.status, run.rows) == ("imported", 6) and run.imported_at is not None
        # The show's page shows the same figures (for those who may read reports).
        page = dashboard.whatnot_show(db.session.get(Show, shop["show"]))
        assert page["ledger"]["netCents"] == 1260


def test_importing_the_same_lines_again_or_an_overlapping_report_changes_nothing(app, shop):
    read(app, shop, orders=False)
    show = shop["wshow"]
    first = [
        earnings("t-1", "1001", show, "21.97", "25.00", "2.00", "1.03"),
        earnings("t-2", "1002", show, "14.85", "17.00", "1.36", "0.79"),
    ]
    assert report(app, shop, first)["new"] == 2
    # The next report overlaps the first (t-2 again) and brings one new line; t-2 is also
    # listed twice in it by mistake.
    second = [first[1], first[1], line("TIP", "t-9", show, "1.00")]
    assert report(app, shop, second) == {"imported": 1, "new": 1, "waiting": 0}
    with app.app_context():
        assert results.figures(shop["org"], whatnot_show_id=show)["netCents"] == 2197 + 1485 + 100
        assert db.session.scalar(select(func.count()).select_from(WhatnotLedgerEntry)) == 3
        # And the next request starts a little before the newest line the app holds.
        start, end = results._span(shop["org"])
        newest = datetime(2026, 11, 8, 3, 20, tzinfo=UTC)
        assert start == newest - results.OVERLAP and end > start


def test_a_file_that_is_not_the_report_is_refused_whole_and_said_so(app, shop):
    read(app, shop, orders=False)
    with app.app_context():
        client = client_for(shop["org"])
        run = results.request(shop["org"], client)
        db.session.commit()
        shop["fake"].finish_report(run.whatnot_id, "<html>Sign in</html>")
        assert results.check(shop["org"], client) == {"imported": 0, "new": 0, "waiting": 0}
        db.session.commit()
        assert db.session.scalar(select(WhatnotReport.status)) == "unreadable"
        assert results.status(shop["org"])["unreadable"] is True
        assert db.session.scalar(select(func.count()).select_from(WhatnotLedgerEntry)) == 0
    for bad in ("", "a,b,c\n1,2,3\n", "ledger_transaction_id,transaction_type\nt-1,TIP\n"):
        with pytest.raises(results.LedgerError):
            results.parse(bad)


def test_the_file_may_open_with_a_title_line_and_odd_lines_are_left_out(app):
    rows = [
        line("TIP", "t-1", "", "2.50"),
        line("TIP", "", "", "9.00"),
        line("TIP", "t-3", "", "lots"),
        line("tip", "t-4", "", "-1", "not a date", quantity_sold="many"),
    ]
    text = "seller_financial_report\n" + ledger_csv(rows)
    got, skipped = results.parse("\ufeff" + text)
    assert skipped == 2
    assert [
        (r["ledger_transaction_id"], r["transaction_type"], r["amount_cents"]) for r in got
    ] == [
        ("t-1", "TIP", 250),
        ("t-4", "TIP", -100),
    ]
    assert got[1]["completed_at"] is None and got[1]["quantity"] is None


def test_a_report_is_asked_for_once_while_one_is_being_built(app, shop):
    read(app, shop, orders=False)
    with app.app_context():
        client = client_for(shop["org"])
        a = results.request(shop["org"], client)
        db.session.commit()
        b = results.request(shop["org"], client)
        assert a.id == b.id and len(shop["fake"].reports) == 1
        span = a.period_end - a.period_start
        assert timedelta(days=34) < span <= timedelta(days=35)  # the first read: five weeks
        assert results.ledger_due(shop["org"]) is True  # one is being built: keep looking


def test_a_report_request_writes_its_times_the_way_whatnot_accepts(app, shop):
    """The first read on the live shop was refused ("invalid-parameter"): a time must be
    written with exactly three decimals and a "Z", not Python's six (or none)."""
    at = datetime(2026, 11, 12, 5, 59, 25, 123456, tzinfo=UTC)
    assert results._stamp(at) == "2026-11-12T05:59:25.123Z"
    assert results._stamp(at.replace(microsecond=0)) == "2026-11-12T05:59:25.000Z"
    new_york = timezone(timedelta(hours=-5))
    assert results._stamp(at.astimezone(new_york)) == "2026-11-12T05:59:25.123Z"
    read(app, shop, orders=False)
    with app.app_context():
        run = results.request(shop["org"], client_for(shop["org"]))  # the fake refuses others
        db.session.commit()
        sent = shop["fake"].reports[run.whatnot_id]["report_parameters"]
        for name in ("start_date", "end_date"):
            assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", sent[name])


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_the_ledger_pages_and_who_may_see_them(app, client, shop, sign_in):
    read(app, shop, orders=False)
    show = shop["wshow"]
    report(app, shop, [earnings("t-1", "1001", show, "21.97", "25.00", "2.00", "1.03")])
    h = sign_in(shop["org"], role="viewer")
    got = client.get("/api/whatnot/ledger").get_json()
    assert got["connected"] is True and got["lines"] == 1 and got["building"] is False
    assert got["shows"] == [
        {
            "showId": shop["show"],
            "title": "Friday night",
            "startedAt": got["shows"][0]["startedAt"],
            "netCents": 2197,
            "lines": 1,
        }
    ]
    assert got["recent"]["netCents"] == 2197 and got["days"] == 30
    page = client.get(f"/api/dashboard/shows/{shop['show']}").get_json()["whatnot"]
    assert page["ledger"]["netCents"] == 2197
    # A viewer only reads; and Whatnot's own ids never reach the page.
    assert client.post("/api/whatnot/ledger/read", headers=h).status_code == 403
    assert show not in client.get("/api/whatnot/ledger").get_data(as_text=True)
    client.post("/api/auth/logout", headers=h)
    h = sign_in(shop["org"], role="fulfillment", email="p@x.test")
    assert client.get("/api/whatnot/ledger").status_code == 403
    assert client.get(f"/api/dashboard/shows/{shop['show']}").get_json()["whatnot"] is None
    client.post("/api/auth/logout", headers=h)
    h = sign_in(shop["org"], role="manager", email="m@x.test")
    r = client.post("/api/whatnot/ledger/read", headers=h)
    assert r.status_code == 200 and r.get_json()["building"] is True
    assert client.post("/api/whatnot/ledger/read").status_code == 403  # no CSRF token


def test_another_shop_sees_none_of_the_ledger(app, client, shop, sign_in):
    from app.models import Organization

    read(app, shop, orders=False)
    report(app, shop, [earnings("t-1", "1001", shop["wshow"], "21.97", "25.00", "2.00", "1.03")])
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        assert results.figures(other_id) is None
    sign_in(other_id)
    assert client.get("/api/whatnot/ledger").get_json() == {"connected": False}
    assert client.get(f"/api/dashboard/shows/{shop['show']}").status_code == 404


def test_amounts_in_another_currency_are_counted_and_said_never_added_in(app, shop):
    read(app, shop, orders=False)
    show = shop["wshow"]
    rows = [
        earnings("t-1", "1001", show, "21.97", "25.00", "2.00", "1.03"),
        line("TIP", "t-2", show, "500.00", transaction_currency="CAD"),
    ]
    report(app, shop, rows)
    with app.app_context():
        got = results.figures(shop["org"], whatnot_show_id=show)
        assert (got["netCents"], got["currency"], got["otherCurrencyLines"]) == (2197, "USD", 1)
        assert [k["kind"] for k in got["kinds"]] == ["ORDER_EARNINGS"]
        assert dashboard.whatnot_ledger(shop["org"])["shows"][0]["netCents"] == 2197


def test_a_large_report_is_stored_in_batches_and_an_unreadable_one_stores_nothing(app, shop):
    read(app, shop, orders=False)
    many = [line("TIP", f"t-{i}", shop["wshow"], "0.01") for i in range(1203)]
    assert report(app, shop, many) == {"imported": 1, "new": 1203, "waiting": 0}
    with app.app_context():
        assert results.figures(shop["org"], whatnot_show_id=shop["wshow"])["netCents"] == 1203
        # The header is checked before anything is stored.
        client = client_for(shop["org"])
        db.session.query(WhatnotReport).update({"status": "failed"})
        run = results.request(shop["org"], client)
        db.session.commit()
        shop["fake"].finish_report(run.whatnot_id, "transaction_type,amount\nTIP,5\n")
        results.check(shop["org"], client)
        db.session.commit()
        assert db.session.scalar(select(func.count()).select_from(WhatnotLedgerEntry)) == 1203


# ---------- regressions from the 2026-11-10 review of this file's code ----------


def test_cost_of_goods_is_per_item_so_it_is_multiplied_by_the_quantity_sold(app, shop):
    """Three sold at a recorded cost of $10.00 each read "$10.00" on the page."""
    read(app, shop, orders=False)
    row = earnings(
        "t-1", "1001", shop["wshow"], "60.00", "25.00", "6.00", "3.00",
        quantity_sold="3", cost_of_goods="10.00",
    )  # fmt: skip
    report(app, shop, [row])
    with app.app_context():
        got = results.figures(shop["org"], whatnot_show_id=shop["wshow"])
        assert (got["costOfGoodsCents"], got["itemPricesCents"]) == (3000, 7500)


def test_lines_the_app_could_not_read_or_that_repeat_an_id_are_counted_and_said(app, shop):
    """They used to vanish: the report read "imported" and the net was simply short."""
    read(app, shop, orders=False)
    show = shop["wshow"]
    rows = [
        earnings("t-1", "1001", show, "21.97", "25.00", "2.00", "1.03"),
        line("ORDER_REFUND", "t-2", show, "twenty"),  # no readable amount
        line("TIP", "t-3", show, "3.00"),
        line("TIP", "t-3", show, "4.00"),  # the same transaction id again
    ]
    assert report(app, shop, rows)["new"] == 2
    with app.app_context():
        run = db.session.scalar(select(WhatnotReport))
        assert (run.rows, run.skipped, run.repeated) == (2, 1, 1)
        status = results.status(shop["org"])
        assert (status["skippedLines"], status["repeatedLines"]) == (1, 1)
        # The first line of a repeated id stands; the second is not added in.
        assert results.figures(shop["org"], whatnot_show_id=show)["netCents"] == 2197 + 300


def test_a_refund_that_names_its_order_but_no_show_counts_for_that_orders_show(app, shop):
    read(app, shop, orders=False)
    show = shop["wshow"]
    rows = [
        earnings("t-1", "1001", show, "21.97", "25.00", "2.00", "1.03"),
        line("ORDER_REFUND", "t-2", "", "-21.97", order_id="1001"),
        line("CARRIER_ADJUSTMENT", "t-3", "", "-1.10"),  # tied to nothing: no show's
        line("ORDER_REFUND", "t-4", "", "-9.00", order_id="7777"),  # another order's
    ]
    report(app, shop, rows)
    with app.app_context():
        assert results.figures(shop["org"], whatnot_show_id=show)["netCents"] == 0
        # The Reports list shows the very same net as the show's own page.
        assert dashboard.whatnot_ledger(shop["org"])["shows"][0]["netCents"] == 0
        since = datetime(2026, 11, 1, tzinfo=UTC)
        assert results.figures(shop["org"], since=since)["netCents"] == -1010


def test_a_report_whatnot_never_finishes_is_given_up_and_a_new_one_asked_for(app, shop):
    read(app, shop, orders=False)
    with app.app_context():
        client = client_for(shop["org"])
        first = results.request(shop["org"], client)
        first.requested_at = datetime.now(UTC) - timedelta(hours=7)
        db.session.commit()
        assert results.open_runs(shop["org"]) == []
        second = results.request(shop["org"], client)
        db.session.commit()
        assert second.id != first.id and len(shop["fake"].reports) == 2
        assert db.session.get(WhatnotReport, first.id).status == "expired"


def test_a_line_dated_in_the_future_does_not_stop_later_reads(app, shop):
    read(app, shop, orders=False)
    report(app, shop, [line("TIP", "t-1", shop["wshow"], "1.00", "2099-01-01T00:00:00Z")])
    with app.app_context():
        start, end = results._span(shop["org"])
        assert start < end and end - start >= timedelta(days=34)


def test_a_file_the_csv_reader_cannot_get_through_is_settled_not_downloaded_forever(app, shop):
    read(app, shop, orders=False)
    with app.app_context():
        client = client_for(shop["org"])
        run = results.request(shop["org"], client)
        db.session.commit()
        ok = line("TIP", "t-1", shop["wshow"], "1.00")
        huge = line("TIP", "t-2", shop["wshow"], "1.00", listing_description="x" * 140_000)
        shop["fake"].finish_report(run.whatnot_id, ledger_csv([ok, huge]))
        assert results.check(shop["org"], client)["imported"] == 0
        db.session.commit()
        assert db.session.get(WhatnotReport, run.id).status == "unreadable"
        assert results.open_runs(shop["org"]) == []
        assert db.session.scalar(select(func.count()).select_from(WhatnotLedgerEntry)) == 0
    rows, skipped = results.parse(
        ledger_csv([line("TIP", "t-9", "", "1.00", quantity_sold="\u00b2")])
    )
    assert (rows[0]["quantity"], skipped) == (None, 0)  # "²" is a digit to Python, not to us


def test_the_page_says_when_the_token_may_not_read_a_shows_results(app, shop):
    read(app, shop, orders=False)
    shop["fake"].scopes = {"read:orders", "read:shows"}
    with app.app_context():
        results.show_results_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
        db.session.commit()
        got = dashboard.whatnot_show(db.session.get(Show, shop["show"]))
        assert got["resultsNotAllowed"] is True and got["results"] is None

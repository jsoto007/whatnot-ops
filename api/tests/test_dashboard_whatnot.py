"""The dashboard's money as Whatnot states it (`dashboard.whatnot_money`).

Against the in-memory fake: every order, ledger line and buyer name here is made up. What
an ORDER_EARNINGS amount holds (before or after its fees) is still not known from a real
file, so these tests check only that the net is the ledger's own sum and that the fees are
Whatnot's listed fees, side by side, never subtracted.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db import db
from app.models import Organization, Show, WhatnotConnection, WhatnotOrder, WhatnotShow
from app.services import dashboard
from app.services.whatnot import results
from tests import test_whatnot_orders as wo
from tests.test_whatnot_results import earnings, line, report

shop = wo.shop  # the connected shop, its show matched to an ended show on Whatnot
read = wo.read


def ago(delta: timedelta) -> str:
    """A time `delta` ago, as Whatnot's financial report writes it."""
    return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(shop, buyer, *prices, status="pending_fulfillment", **kw):
    """One Whatnot order in the shop's show: one item per price (or (price, quantity))."""
    items = [
        {"title": f"Lot {i}", "price_cents": p[0], "quantity": p[1]}
        if isinstance(p, tuple)
        else {"title": f"Lot {i}", "price_cents": p}
        for i, p in enumerate(prices, start=1)
    ]
    kw.setdefault("created", datetime.now(UTC) - timedelta(minutes=30))
    return shop["fake"].add_order(
        show_id=shop["wshow"], buyer=buyer, items=items, status=status, **kw
    )


def money(app, shop, days=30):
    with app.app_context():
        return dashboard.whatnot_money(shop["org"], days=days)


def test_sales_are_whatnots_paid_orders_item_prices_in_cents(app, shop):
    """Worked example, in cents, every order in the last 30 days unless said:
      mika_r  paid: 2 x $12.50                    -> 2500, 2 items
      mika_r  paid: $17.00                        -> 1700, 1 item
      jo.b    paid: $8.45                         ->  845, 1 item
      sam_t   payment still being taken: $10.00   -> pending 1000
      ana.k   failed: $8.00                       -> failed 800
      ana.k   cancelled: $5.00                    -> cancelled 500
      lu.w    a giveaway ($0)                     -> counted, never summed
      vee     paid $99.00, but 40 days ago        -> only in the 90 days
      cad.co  paid $50.00 in CAD                  -> counted, never added in
    Sales 2500 + 1700 + 845 = 5045 over 3 orders and 2 buyers; the average order is
    5045 / 3 = 1681.67, so 1682. Shipping ($5.25 an order) is never in the sales."""
    add(shop, "mika_r", (1250, 2))
    add(shop, "mika_r", 1700)
    add(shop, "jo.b", 845)
    add(shop, "sam_t", 1000, status="pending")
    add(shop, "ana.k", 800, status="failed")
    add(shop, "ana.k", 500, status="cancelled")
    add(shop, "lu.w", 0, transaction_type="giveaway")
    old = add(shop, "vee", 9900)
    cad = add(shop, "cad.co", 5000)
    cad["amounts"]["total"]["currency"] = "CAD"
    read(app, shop)
    with app.app_context():
        db.session.execute(
            update(WhatnotOrder)
            .where(WhatnotOrder.whatnot_id == old["id"])
            .values(ordered_at=datetime.now(UTC) - timedelta(days=40))
        )
        db.session.commit()
    got = money(app, shop)
    assert got["connected"] is True and (got["days"], got["currency"]) == (30, "USD")
    assert got["sales"] == {
        "paidCents": 5045,
        "orders": 3,
        "items": 4,
        "buyers": 2,
        "averageOrderCents": 1682,
        "pendingCents": 1000,
        "pendingOrders": 1,
        "failedCents": 800,
        "failedOrders": 1,
        "retriedOrders": 0,
        "cancelledCents": 500,
        "cancelledOrders": 1,
        "giveaways": 1,
        "otherCurrencyOrders": 1,
    }
    assert got["ordersReadAt"] is not None
    longer = money(app, shop, days=90)["sales"]
    assert (longer["paidCents"], longer["orders"], longer["buyers"]) == (5045 + 9900, 4, 3)


def test_top_buyers_are_by_what_they_paid_one_row_per_whatnot_account(app, shop):
    """A buyer who changed their name is one row, under the name they use now; a buyer
    whose every order failed, and a giveaway winner, are not buyers here."""
    now = datetime.now(UTC)
    add(shop, "old_name", 2000, buyer_id="u-100", created=now - timedelta(hours=2))
    add(shop, "new_name", 1500, buyer_id="u-100", created=now - timedelta(hours=1))
    add(shop, "kim.p", 3000)
    add(shop, "kim.p", 900, status="failed")
    add(shop, "only.fails", 9900, status="failed")
    add(shop, "lu.w", 0, transaction_type="giveaway")
    add(shop, "sam_t", 4000, status="pending")
    read(app, shop)
    assert money(app, shop)["topBuyers"] == [
        {"buyer": "new_name", "paidCents": 3500, "orders": 2, "shows": 1, "failedOrders": 0},
        {"buyer": "kim.p", "paidCents": 3000, "orders": 1, "shows": 1, "failedOrders": 1},
    ]
    assert money(app, shop)["sales"]["buyers"] == 2


def test_a_card_declined_then_paid_on_a_retry_is_a_paid_win_not_a_failed_payment(app, shop):
    """The 2026-11-13 show: half the lots were a declined card, then a second order for the
    same lot, paid minutes later. Those failed orders are history (Compare and Apply skip
    them the same way); a decline nobody paid afterwards, and a payment for another lot,
    still count as failed."""
    now = datetime.now(UTC)
    add(shop, "mika_r", 2500, status="failed", created=now - timedelta(minutes=50))
    add(shop, "mika_r", 2500, created=now - timedelta(minutes=48))  # the retry, paid
    add(shop, "jo.b", 1500, status="failed", created=now - timedelta(minutes=40))
    add(shop, "jo.b", 900, created=now - timedelta(minutes=39))  # another lot's price
    add(shop, "sam_t", 1200, status="failed", created=now - timedelta(minutes=30))
    read(app, shop)
    got = money(app, shop)
    s = got["sales"]
    assert (s["failedOrders"], s["failedCents"], s["retriedOrders"]) == (2, 2700, 1)
    assert (s["orders"], s["paidCents"]) == (2, 3400)
    failed = {b["buyer"]: b["failedOrders"] for b in got["topBuyers"]}
    assert failed == {"mika_r": 0, "jo.b": 1}  # sam_t paid nothing: not a top buyer


def test_the_net_is_the_ledgers_own_sum_and_the_fees_are_whatnots_listed_fees(app, shop):
    """The period's ledger, in cents:
      order 1001 earnings  +21.97 (item 25.00, commission 2.00, processing 1.03)
      order 1001 refunded   -2.00 (names its order, no show)
      a tip, 40 days ago    +5.00 (out of the 30 days; still the show's)
    Net for 30 days: 2197 - 200 = 1997. Fees: 200 + 103 = 303, listed, not subtracted.
    The show's own net (every line it has): 1997 + 500 = 2497, the same as its page."""
    add(shop, "mika_r", 2500)
    read(app, shop)
    report(
        app,
        shop,
        [
            earnings(
                "t-1", "1001", shop["wshow"], "21.97", "25.00", "2.00", "1.03",
                transaction_completed_at_utc=ago(timedelta(hours=1)),
            ),
            line("ORDER_REFUND", "t-2", "", "-2.00", ago(timedelta(minutes=30)), order_id="1001"),
            line("TIP", "t-3", shop["wshow"], "5.00", ago(timedelta(days=40))),
        ],
    )  # fmt: skip
    got = money(app, shop)
    assert got["ledger"]["netCents"] == 1997
    assert got["feesCents"] == 303
    assert got["ledgerStatus"]["lines"] == 3 and got["ledgerStatus"]["notAllowed"] is False
    assert got["shows"] == [
        {
            "showId": shop["show"],
            "paidCents": 2500,
            "pendingCents": 0,
            "orders": 1,
            "items": 1,
            "buyers": 1,
            "netCents": 2497,
        }
    ]
    with app.app_context():
        page = dashboard.whatnot_show(db.session.get(Show, shop["show"]))
        assert page["ledger"]["netCents"] == 2497


def test_each_shows_net_in_one_query_is_what_its_own_page_says(app, shop):
    """`nets_by_show` against `figures` for two shows: a line naming no show counts for
    the show its order's lines name, a line in another currency is never added in, and a
    line tied to nothing is nobody's."""
    read(app, shop, orders=False)
    a, b, c = shop["wshow"], "another-show", "a-show-only-in-cad"
    report(
        app,
        shop,
        [
            earnings("t-1", "1001", a, "21.97", "25.00", "2.00", "1.03"),
            earnings("t-2", "2001", b, "14.85", "17.00", "1.36", "0.79"),
            line("ORDER_REFUND", "t-3", "", "-21.97", order_id="1001"),
            line("SHIPPING_CHARGE", "t-4", "", "-5.25", order_id="2001"),
            line("TIP", "t-5", a, "500.00", transaction_currency="CAD"),
            line("CARRIER_ADJUSTMENT", "t-6", "", "-1.10"),
            line("TIP", "t-7", c, "9.00", transaction_currency="CAD"),
        ],
    )
    with app.app_context():
        nets = results.nets_by_show(shop["org"], [a, b, c, "a-show-with-no-lines"])
        assert nets == {a: 0, b: 1485 - 525, c: 0}
        assert results.figures(shop["org"], whatnot_show_id="a-show-with-no-lines") is None
        for wid in (a, b, c):
            assert nets[wid] == results.figures(shop["org"], whatnot_show_id=wid)["netCents"]
        assert results.nets_by_show(shop["org"], []) == {}


def test_who_may_see_it_and_what_never_reaches_the_page(app, client, shop, sign_in):
    add(shop, "mika_r", 2500, buyer_id="u-4242")
    read(app, shop)
    h = sign_in(shop["org"], role="viewer")
    r = client.get("/api/dashboard/whatnot?days=7")
    assert r.status_code == 200 and r.get_json()["sales"]["paidCents"] == 2500
    body = r.get_data(as_text=True)
    # Whatnot's own ids (the show's, the buyer's account) stay on the server.
    assert shop["wshow"] not in body and "u-4242" not in body
    assert client.get("/api/dashboard/whatnot?days=14").status_code == 400
    assert client.get("/api/dashboard/whatnot").get_json()["days"] == 30
    client.post("/api/auth/logout", headers=h)
    sign_in(shop["org"], role="fulfillment", email="p@x.test")
    assert client.get("/api/dashboard/whatnot").status_code == 403  # money: not packing


def test_without_a_working_connection_the_page_keeps_its_own_numbers(app, client, shop, sign_in):
    add(shop, "mika_r", 2500)
    read(app, shop)
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
        assert dashboard.whatnot_money(other_id) == {"connected": False}
    sign_in(other_id)
    assert client.get("/api/dashboard/whatnot").get_json() == {"connected": False}
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        conn.disconnected_at = datetime.now(UTC)
        db.session.commit()
    assert money(app, shop) == {"connected": False}


def test_an_order_whatnot_gave_no_amount_for_does_not_lower_the_average(app, shop):
    """$10.00 and $5.00 paid, and a paid order with no amount: 3 orders, $15.00 sold, and
    the average is over the two with an amount: $7.50, not $5.00."""
    add(shop, "mika_r", 1000)
    add(shop, "jo.b", 500)
    blank = add(shop, "sam_t", 700)
    read(app, shop)
    with app.app_context():
        db.session.execute(
            update(WhatnotOrder)
            .where(WhatnotOrder.whatnot_id == blank["id"])
            .values(subtotal_cents=None)
        )
        db.session.commit()
    s = money(app, shop)["sales"]
    assert (s["orders"], s["paidCents"], s["averageOrderCents"]) == (3, 1500, 750)


def test_two_recordings_of_one_whatnot_show_never_count_it_twice(app, shop):
    """Two laptops recorded the same show: its figures go to the first recording, and the
    second says which one carries them."""
    add(shop, "mika_r", 1000)
    read(app, shop)
    with app.app_context():
        first = db.session.get(Show, shop["show"])
        second = Show(
            organization_id=first.organization_id,
            show_key="second-laptop",
            started_at=first.started_at + timedelta(minutes=2),
            show_date=first.show_date,
            whatnot_show_id=first.whatnot_show_id,
        )
        db.session.add(second)
        db.session.commit()
        second_id = second.id
    shows = money(app, shop)["shows"]
    assert {"showId": second_id, "sameAsShowId": shop["show"]} in shows
    mine = [r for r in shows if r["showId"] == shop["show"]]
    assert mine[0]["paidCents"] == 1000 and sum(r.get("paidCents", 0) for r in shows) == 1000


@pytest.mark.parametrize("days", dashboard.PERIODS)
def test_a_shop_connected_with_nothing_read_yet_shows_zeros_not_an_error(app, shop, days):
    got = money(app, shop, days=days)
    assert got["sales"]["paidCents"] == 0 and got["sales"]["averageOrderCents"] == 0
    assert got["topBuyers"] == [] and got["ledger"] is None and got["feesCents"] is None
    assert got["newBuyers"] is None and got["shows"] == []


def test_new_buyers_are_whatnots_own_count_from_the_shows_it_has_worked_out(app, shop):
    read(app, shop, orders=False)
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"]))
        ws.analytics = {"first_time_buyers_count": 19, "buyers_count": 73}
        db.session.commit()
    got = money(app, shop)
    assert (got["newBuyers"], got["newBuyersShows"]) == (19, 1)

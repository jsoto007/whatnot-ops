"""Review comments on PR 79, each pinned by a test.

- C2: a card declined at the win and paid on a retry is one win even when the two orders'
  prices differ only by a discount one of them carries ($25.00 failed, $23.70 paid after
  $1.30 off, or the reverse): paired by the buyer's name (`_skip_retried`) or held for a
  paid order with no buyer (`_hold_for_nameless`). An exact price is still the surer pair,
  a different listing or a gap no discount explains is still not one, and the checks that
  run after a pairing (`_own_win_first`, `_flag_twin`) read the discount too.

- M2 (the owner's call, 2026-11-19): when a buyer used a discount, the app's own money (the
  tiles, the Revenue and Customers sheets, the Buyers page figures, the top buyers) is what
  Whatnot charged for the win, its own order line after the discount, whichever arrived
  first, the win line or the order. The sale's stored price is never rewritten.

The backfill's end of the list (C3) is pinned in `test_whatnot_backfill.py`, the gifts tile
of a show read in full with no giveaway (C4) in `test_reports.py`, the shipping beside the
dashboard's Whatnot sales in `test_dashboard_whatnot.py`.

Every buyer name here is made up; Whatnot is the in-memory fake.
"""

from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.db import db
from app.models import (
    Organization,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotOrderItem,
)
from app.services import catalog, dashboard, pipeline, reports
from app.services.inventory import Actor
from app.services.reconcile import Row
from app.services.whatnot import orders as whatnot_orders
from tests import test_whatnot_orders as wo
from tests.test_one_win_per_auction import listed
from tests.test_pipeline import level, run
from tests.test_whatnot_decides import LOT as DECIDES_LOT
from tests.test_whatnot_decides import followed, item_of, reasons, states
from tests.test_whatnot_orders import apply, compare, order, read, win
from tests.test_whatnot_reads import nameless_order

shop = wo.shop

ANN, BEA = "made_up_ann", "made_up_bea"


def _filler(shop, n=6):
    """Named orders of other lots, so one order with no buyer is not most of the show's."""
    for i in range(n):
        order(shop, f"made_up_filler_{i}", f"Lot {i}")


def _failed(shop, buyer, title, price=2500, **kw):
    o = order(shop, buyer, title, price, **kw)
    shop["fake"].set_order_status(o["id"], "failed")
    return o


def _sales(app, buyer=ANN):
    with app.app_context():
        return [
            (s.status, s.external_order_id is not None)
            for s in db.session.scalars(
                select(Sale).where(Sale.buyer_username == buyer).order_by(Sale.id)
            )
        ]


# ---------- C2: a retry whose price differs only by a discount is the same win ----------


@pytest.mark.parametrize(
    "failed_off, paid_off",
    [(0, 130), (130, 0)],
    ids=["failed-full-paid-discounted", "failed-discounted-paid-full"],
)
def test_c2_a_retry_whose_price_differs_only_by_its_discount_is_one_win_paid(
    app, shop, failed_off, paid_off
):
    """$25.00 failed with no discount, then $23.70 paid after $1.30 off (and the reverse):
    one win, paid; neither declined nor recorded a second time."""
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    _failed(shop, ANN, "Red bundle", discount_cents=failed_off, created=now - timedelta(seconds=90))
    order(shop, ANN, "Red bundle", discount_cents=paid_off, created=now - timedelta(seconds=20))
    read(app, shop)
    rows, _, _ = compare(app, shop)
    assert [a for _, s, a in rows if s == "failed"] == ["skip"]
    summary = apply(app, shop)
    assert "decline" not in summary and "recorded" not in summary
    assert _sales(app) == [(SaleStatus.paid, True)]
    assert level(app, shop["red"]) == (2, 0)  # one unit out for the one win


@pytest.mark.parametrize(
    "failed_off, paid_off",
    [(0, 130), (130, 0)],
    ids=["failed-full-paid-discounted", "failed-discounted-paid-full"],
)
def test_c2_a_paid_order_with_no_buyer_at_a_discount_is_held_as_the_retry(
    app, shop, failed_off, paid_off
):
    """The paying order has no buyer (a closed account): it may be the retry when only a
    discount separates the two prices, so the declined attempt does not take the sale back."""
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    _failed(shop, ANN, "Red bundle", discount_cents=failed_off, created=now - timedelta(seconds=40))
    nameless_order(shop, "Red bundle", discount_cents=paid_off, created=now - timedelta(seconds=10))
    _filler(shop)
    read(app, shop)
    summary = apply(app, shop)
    assert "decline" not in summary
    assert _sales(app)[0][0] is SaleStatus.paid


def test_c2_a_different_listing_is_still_never_the_retry_even_at_a_discount(app, shop):
    """Two auctions alike in title and price, told apart by Whatnot's listing ids: the
    discount explains the price gap, but a payment for one listing is never the retry of a
    card declined on the other."""
    read(app, shop)
    shop["say"](app, [win(ANN, "Red bundle"), win(ANN, "Red bundle")])
    run(app, shop["show"])
    first = listed(shop, ANN, "Red bundle", "listing-1", created=datetime.now(UTC))
    shop["fake"].set_order_status(first["id"], "failed")
    listed(
        shop, ANN, "Red bundle", "listing-2", discount_cents=130,
        created=datetime.now(UTC) + timedelta(minutes=3),
    )  # fmt: skip
    read(app, shop)
    rows, _, _ = compare(app, shop)
    assert [a for _, s, a in rows if s == "failed"] == ["decline"]
    apply(app, shop)
    assert sorted(s.value for s, _ in _sales(app)) == ["paid", "payment_failed"]


@pytest.mark.parametrize("paid_price", [2300, 2400])
def test_c2_a_gap_no_discount_explains_is_still_not_the_retry(app, shop, paid_price):
    """$25.00 failed; the payment is $23.00 or $24.00 before its $1.30 off ($21.70 / $22.70
    charged): $1.30 does not close a gap of $3.30 or $2.30, so it is not that win's retry.
    The declined attempt is a decline, named or held for no buyer alike."""
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    _failed(shop, ANN, "Red bundle", created=now - timedelta(seconds=90))
    order(
        shop, ANN, "Red bundle", paid_price, discount_cents=130, created=now - timedelta(seconds=20)
    )
    nameless_order(
        shop, "Red bundle", paid_price, discount_cents=130, created=now - timedelta(seconds=10)
    )
    _filler(shop)
    read(app, shop)
    rows, _, _ = compare(app, shop)
    assert [a for _, s, a in rows if s == "failed"] == ["decline"]


@pytest.mark.parametrize(
    "failed_off, paid_off",
    [(0, 130), (130, 0)],
    ids=["failed-full-paid-discounted", "failed-discounted-paid-full"],
)
def test_c2_a_discounted_retry_paid_after_the_decline_was_applied_restores_the_sale(
    app, shop, failed_off, paid_off
):
    """Orders one at a time ("auto"): the failed order was applied first and declined the
    win; the retry, paid at a price only a discount separates, then pays that same sale
    again (`test_a_retry_paid_after_the_decline_was_applied_restores_the_sale` at a
    discount). One sale, paid; never a second."""
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    _failed(shop, ANN, "Red bundle", discount_cents=failed_off, created=now - timedelta(seconds=90))
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    assert level(app, shop["red"]) == (3, 0)
    order(shop, ANN, "Red bundle", discount_cents=paid_off, created=now - timedelta(seconds=20))
    read(app, shop)
    assert apply(app, shop) == {"mark_paid": 1, "skip": 1}
    assert _sales(app) == [(SaleStatus.paid, True)]
    assert level(app, shop["red"]) == (2, 0)


@pytest.mark.parametrize(
    "failed_off, paid_off",
    [(0, 100), (100, 0)],
    ids=["failed-full-paid-discounted", "failed-discounted-paid-full"],
)
def test_c2_a_discounted_retry_of_a_declined_win_with_no_product_waits_for_its_product(
    app, shop, failed_off, paid_off
):
    """`test_a_declined_win_with_no_product_paid_on_a_retry_waits_for_its_product_again` at a
    discount: a pick-your-own win at $11.00 declined by Whatnot, then paid $10.00 after $1.00
    off (or the reverse). Still the one sale, waiting for its product again."""
    shop["say"](app, [win(ANN, DECIDES_LOT, "11.00")])
    run(app, shop["show"])
    now = datetime.now(UTC)
    _failed(
        shop, ANN, DECIDES_LOT, 1100, discount_cents=failed_off,
        created=now - timedelta(seconds=90),
    )  # fmt: skip
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    paid = order(shop, ANN, DECIDES_LOT, 1100, discount_cents=paid_off)
    read(app, shop)
    assert apply(app, shop) == {"reopen": 1, "skip": 1}
    with app.app_context():
        [sale] = db.session.scalars(select(Sale)).all()  # still the one sale
        assert (sale.status, sale.sku_id) == (SaleStatus.detected, None)
        assert sale.external_order_id == paid["id"]
    assert reasons(app) == ["unmatched_item"]  # "which product is that?" is open again


def test_c2_an_exact_price_retry_is_still_the_surer_pair_than_a_discounted_payment(app):
    """One buyer's card failed at $25.00; two later payments of the listing: a nearer one at
    $23.70 after $1.30 off, then one at exactly $25.00. The exact one is the retry (as before
    discounts were read); the discounted one stays a payment of its own."""
    now = datetime.now(UTC)

    def row(status, price, ago, off=None):
        return Row(
            buyer=ANN, item="Red bundle", price_cents=price, status=status,
            ordered_at=now - timedelta(seconds=ago), discount_cents=off,
        )  # fmt: skip

    failed = row("failed", 2500, 90)
    discounted = row("paid", 2370, 60, off=130)
    exact = row("paid", 2500, 20)
    with app.app_context():
        whatnot_orders._skip_retried([failed, discounted, exact])
    assert (failed.action, failed.note) == ("skip", whatnot_orders.RETRIED)
    assert exact.first_ordered_at == failed.ordered_at
    assert discounted.first_ordered_at is None


def test_c2_two_wins_the_first_declined_the_second_paid_at_a_discount_stays_settled(app, shop):
    """`test_two_wins_of_one_listing_the_first_declined_the_second_paid_is_settled` with the
    second win's payment after a discount: once the two orders pair, the buyer's second win
    (standing, no order of its own) must still take the payment, and the first stays
    declined and flagged (`_own_win_first` reads the discount too)."""
    followed(app, shop)
    shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    read(app, shop)
    shop["say"](app, [win(ANN, "Red bundle")])
    run(app, shop["show"])
    _failed(shop, ANN, "Red bundle", created=datetime.now(UTC))
    read(app, shop)
    assert apply(app, shop) == {"decline": 1}
    shop["say"](app, [win(ANN, "Red bundle")])  # the second win, at $25.00
    run(app, shop["show"])
    order(
        shop, ANN, "Red bundle", discount_cents=130,
        created=datetime.now(UTC) + timedelta(minutes=3),
    )  # fmt: skip
    read(app, shop)
    apply(app, shop)
    assert states(app) == [(ANN, "payment_failed"), (ANN, "paid")]
    assert reasons(app) == ["payment_declined"]
    assert level(app, shop["red"]) == (2, 0)


def test_c2_two_wins_read_together_at_a_discount_are_put_to_a_person(app, shop):
    """The same two wins with the failed and the discounted paid order in one read: as at
    an exact price, the payment goes to the first and the win left with no order is put to a
    person at once (`_flag_twin` reads the discount too), never left standing unflagged."""
    followed(app, shop)
    shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    read(app, shop)
    shop["say"](app, [win(ANN, "Red bundle"), win(ANN, "Red bundle")])
    run(app, shop["show"])
    _failed(shop, ANN, "Red bundle", created=datetime.now(UTC))
    order(
        shop, ANN, "Red bundle", discount_cents=130,
        created=datetime.now(UTC) + timedelta(minutes=3),
    )  # fmt: skip
    read(app, shop)
    apply(app, shop)
    assert states(app) == [(ANN, "paid"), (ANN, "paid")]
    assert reasons(app) == ["no_payment_record"]
    with app.app_context():  # the price the wins were won at, not the one after the discount
        assert "twice at $25.00" in item_of("no_payment_record").summary


def test_c2_two_different_discounts_on_the_two_attempts_are_not_paired(app):
    """What the comparison cannot see (left as is, `pipeline.prices_agree` is shared): both
    attempts carry a discount of their own ($24.00 after $1.00 off, $23.70 after $1.30 off,
    both $25.00 before). Each side may only be lower than the other by its own discount, so
    the $0.30 between them is explained by neither."""
    assert (
        pipeline.same_win(
            "Red bundle",
            2400,
            None,
            "Red bundle",
            2370,
            None,
            discount_a=pipeline.unit_discount(100, 1, 1),
            discount_b=pipeline.unit_discount(130, 1, 1),
        )
        is None
    )


# ---------- M2: the app's own money is what Whatnot charged ----------


def _paid_everywhere(app, shop, buyer=ANN) -> tuple[int, ...]:
    """The buyer's paid money as each of the app's own places reads it, in cents: the show's
    "Paid revenue" tile, the Buyers page figures, the top buyers, and the workbook's Revenue
    and Customers sheets (written in dollars)."""
    with app.app_context():
        org = shop["org"]
        show = db.session.get(Show, shop["show"])
        tile = dashboard.show_tiles(show)["revenuePaidCents"]
        figures = dashboard.buyer_figures(org)[buyer]["paidCents"]
        [top] = [b["paidCents"] for b in dashboard.top_buyers(org) if b["buyer"] == buyer]
        book = load_workbook(BytesIO(reports.build(db.session.get(Organization, org), show=show)))
    revenue = list(book["Revenue"].iter_rows(values_only=True))[1][4]
    [customer] = [r[2] for r in book["Customers"].iter_rows(values_only=True) if r[0] == buyer]
    return tile, figures, top, round(revenue * 100), round(customer * 100)


def _ties(app, buyer=ANN) -> list[tuple]:
    """The premise of each test, per sale of the buyer: (status, the stored price, tied to a
    Whatnot order, that order's line price for it)."""
    with app.app_context():
        out = []
        for s in db.session.scalars(
            select(Sale).where(Sale.buyer_username == buyer).order_by(Sale.id)
        ):
            line = db.session.scalar(
                select(WhatnotOrderItem.unit_price_cents).where(WhatnotOrderItem.sale_id == s.id)
            )
            out.append((s.status, s.price_cents, s.external_order_id is not None, line))
        return out


def test_m2_a_win_line_then_its_order_less_a_discount_counts_what_whatnot_charged(app, shop):
    """Hand trace: the video player shows the win at $25.00 (2500); Whatnot's order takes
    $1.25 off, so it charged 2500 - 125 = 2375. The sale keeps 2500 (a decline or a second
    laptop's copy of the win is looked up by it); every paid figure reads 2375."""
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    order(shop, ANN, "Red bundle", 2500, discount_cents=125)
    read(app, shop)
    apply(app, shop)
    assert _ties(app) == [(SaleStatus.paid, 2500, True, 2375)]  # stored price untouched
    assert _paid_everywhere(app, shop) == (2375,) * 5


def test_m2_the_order_then_its_win_line_counts_the_same(app, shop):
    """The other way round: the order arrives first (its sale carries 2375), then the player's
    line at $25.00 is claimed by it. One sale, and the same 2375 everywhere."""
    order(shop, ANN, "Red bundle", 2500, discount_cents=125)
    read(app, shop)
    apply(app, shop)
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    assert _ties(app) == [(SaleStatus.paid, 2375, True, 2375)]
    assert _paid_everywhere(app, shop) == (2375,) * 5


def test_m2_a_win_with_no_order_counts_its_own_price(app, shop):
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    assert _ties(app) == [(SaleStatus.paid, 2500, False, None)]
    assert _paid_everywhere(app, shop) == (2500,) * 5


def test_m2_an_order_of_several_items_counts_each_lines_own_price(app, shop):
    """Hand trace: two wins, Red bundle $25.00 and Blue thing $10.00, then ONE order of both
    with $1.25 off the order. Whatnot states each line after the discount (the fake puts it
    on the first): 2375 and 1000. Paid revenue 2375 + 1000 = 3375, not 3500. (A person is
    asked about the gap on the first: a several-item order's discount cannot be placed by
    the app; the money is still each line's own stated price.)"""
    with app.app_context():
        catalog.create_product(
            shop["org"], "Blue thing", skus=[{"code": "BLUE", "priceCents": 1000}]
        )
        db.session.commit()
    shop["say"](app, [win(ANN, "Red bundle", "25.00"), win(ANN, "Blue thing", "10.00")])
    run(app, shop["show"])
    shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer=ANN,
        items=[
            {"title": "Red bundle", "price_cents": 2500},
            {"title": "Blue thing", "price_cents": 1000},
        ],
        discount_cents=125,
        created=datetime.now(UTC) - timedelta(seconds=20),
    )
    read(app, shop)
    apply(app, shop)
    assert _ties(app) == [
        (SaleStatus.paid, 2500, True, 2375),
        (SaleStatus.paid, 1000, True, 1000),
    ]
    assert _paid_everywhere(app, shop) == (3375,) * 5


def test_m2_a_line_with_no_price_falls_back_to_the_wins_own_price(app, shop):
    """Whatnot gave no price for the line (unread): the win's own price counts, never 0."""
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    order(shop, ANN, "Red bundle", 2500, discount_cents=125)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        db.session.execute(WhatnotOrderItem.__table__.update().values(unit_price_cents=None))
        db.session.commit()
    assert _ties(app) == [(SaleStatus.paid, 2500, True, None)]
    assert _paid_everywhere(app, shop) == (2500,) * 5


def test_m2_only_the_sales_own_order_counts_never_a_declined_attempt(app, shop):
    """A card declined at $25.00 and paid on a retry at $23.75 after $1.25 off: two orders
    on Whatnot, and the declined one's line may still name the sale. Only the order the
    sale is tied to counts: 2375 once, never 2500 + 2375."""
    shop["say"](app, [win(ANN, "Red bundle", "25.00")])
    run(app, shop["show"])
    _failed(shop, ANN, "Red bundle", created=datetime.now(UTC) - timedelta(seconds=30))
    read(app, shop)
    apply(app, shop)
    order(shop, ANN, "Red bundle", 2500, discount_cents=125)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        [sale] = db.session.scalars(select(Sale)).all()
        lines = db.session.scalars(
            select(WhatnotOrderItem.unit_price_cents)
            .where(WhatnotOrderItem.sale_id == sale.id)
            .order_by(WhatnotOrderItem.id)
        ).all()
        assert sale.status is SaleStatus.paid and sale.price_cents == 2500
    assert lines == [2500, 2375]  # the premise: both attempts name the sale
    assert _paid_everywhere(app, shop) == (2375,) * 5


def test_m2_a_win_to_pick_counts_what_whatnot_charged_and_keeps_it_once_named(app, shop):
    """A "choose any" lot won at $25.00 on the player, its order $1.25 off: to pick, 2375.
    Once a person names the product it is paid here, at the same 2375, and "sold so far"
    does not move."""
    lot = "CHOOSE ANY PRODUCT YOU WANT #12"
    shop["say"](app, [win(ANN, lot, "25.00")])
    run(app, shop["show"])
    order(shop, ANN, lot, 2500, discount_cents=125)
    read(app, shop)
    apply(app, shop)
    assert _ties(app) == [(SaleStatus.detected, 2500, True, 2375)]
    with app.app_context():
        t = dashboard.show_tiles(db.session.get(Show, shop["show"]))
    assert (t["toPick"], t["revenueToPickCents"], t["revenuePaidCents"]) == (1, 2375, 0)
    assert t["revenueSoldCents"] == 2375
    with app.app_context():
        item = db.session.scalar(
            select(ReviewItem).where(
                ReviewItem.reason == "unmatched_item", ReviewItem.status == ReviewStatus.open
            )
        )
        pipeline.resolve(
            item, "assign_sku", {"skuId": shop["red"]}, user=None, actor=Actor("user", None, "M")
        )
        db.session.commit()
    assert _ties(app) == [(SaleStatus.paid, 2500, True, 2375)]
    with app.app_context():
        t = dashboard.show_tiles(db.session.get(Show, shop["show"]))
    assert (t["toPick"], t["revenueToPickCents"], t["revenuePaidCents"]) == (0, 0, 2375)
    assert t["revenueSoldCents"] == 2375


def test_m2_the_customers_sheet_adds_up_to_paid_revenue_a_settled_win_included(
    app, client, shop, sign_in
):
    """The workbook's per-buyer "Paid" is what the Revenue sheet's "Paid revenue" adds up.
    Hand trace for one buyer: a win settled with no product (its listing "Not one of ours",
    its question ignored), its order $15.00 less $1.00, so 1400; and a named win shown at
    $25.00 on the player whose order took $1.25 off, so 2375. 1400 + 2375 = 3775 on the
    tile, the Buyers page, the top buyers and both sheets."""
    from tests.test_money_review_fixes import ZED, _settled_win

    _settled_win(app, client, shop, sign_in, discount_cents=100)
    shop["say"](app, [win(ZED, "Red bundle", "25.00")])
    run(app, shop["show"])
    order(shop, ZED, "Red bundle", 2500, discount_cents=125)
    read(app, shop)
    apply(app, shop)
    assert _ties(app, ZED) == [
        (SaleStatus.detected, 1400, True, 1400),  # settled; its order made it, at 1400
        (SaleStatus.paid, 2500, True, 2375),
    ]
    assert _paid_everywhere(app, shop, ZED) == (3775,) * 5

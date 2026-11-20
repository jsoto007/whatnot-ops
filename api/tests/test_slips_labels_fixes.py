"""Small correctness fixes on the order slips and the labels (end-to-end check 2026-11-15,
findings V4, A14, A37-A45, A61). Against the in-memory fake; buyers are made up."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import db
from app.models import (
    Job,
    Order,
    OrderStatus,
    ReviewItem,
    ReviewStatus,
    Sale,
    SaleStatus,
    Show,
    WhatnotLot,
    WhatnotShipment,
)
from app.services import order_slips, orders, pipeline
from app.services.whatnot import shipping
from tests import test_whatnot_orders as wo
from tests.test_label_printing import labelled, labels, press  # noqa: F401  (a fixture)
from tests.test_pipeline import run
from tests.test_whatnot_orders import apply, read
from tests.test_whatnot_shipping import parcel, read_shipments, view

shop = wo.shop  # the same connected shop and matched show as the orders tests


def slips(app, shop):
    with app.app_context():
        doc = order_slips.build(db.session.get(Show, shop["show"]))
        db.session.rollback()
        return doc


def test_two_items_of_one_order_each_match_their_own_sale(app, shop):
    """V4 + A39: one Whatnot order with two items is two sales with one order number, and two
    lots ("<order>" and "<order>-2"). Each lot must find its own sale, not the last one."""
    o = shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer="lani808",
        items=[
            {"title": "Red bundle", "price_cents": 2500},
            {"title": "Blue bundle", "price_cents": 900},
        ],
    )
    parcel(shop, "lani808", [o])
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    with app.app_context():
        sales = db.session.scalars(select(Sale).order_by(Sale.id)).all()
        assert [s.external_order_id for s in sales] == [o["id"], o["id"]]
    doc = slips(app, shop)
    rows = doc["slips"][0]["rows"]
    with app.app_context():
        name = {s.id: s.item_text for s in db.session.scalars(select(Sale))}
    assert sorted((name[r["saleId"]], r["priceCents"], r["inApp"]) for r in rows) == [
        ("Blue bundle", 900, True),
        ("Red bundle", 2500, True),
    ]
    assert doc["whatnot"]["lotsNotInApp"] == 0 and doc["whatnot"]["salesNotOnWhatnot"] == 0
    # The second item is its own lot ("<order>-2"), tied to its own sale.
    assert sorted(r["whatnotOrder"] for r in rows) == [o["id"], o["id"] + "-2"]
    with app.app_context():
        lots = {lot.title: lot for lot in db.session.scalars(select(WhatnotLot))}
        sales = {s.item_text: s for s in db.session.scalars(select(Sale))}
        matched = order_slips.match_lots(list(lots.values()), list(sales.values()))
        assert {lot.title: matched[lot.id].item_text for lot in lots.values()} == {
            "Red bundle": "Red bundle",
            "Blue bundle": "Blue bundle",
        }


def test_a_lot_of_two_units_prints_the_same_price_with_or_without_the_whatnot_lots(app, shop):
    """A37: the Notes price is the whole lot's, once on its first row, whether the slip comes
    from Whatnot's lots or from the app's sales."""
    o = shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer="lani808",
        items=[{"title": "Red bundle", "price_cents": 1000, "quantity": 2}],
    )
    parcel(shop, "lani808", [o])
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    with_lots = slips(app, shop)["slips"][0]["rows"]
    assert [(r["priceCents"], r["continued"]) for r in with_lots] == [(2000, False), (None, True)]
    with app.app_context():
        db.session.execute(WhatnotLot.__table__.delete())
        db.session.commit()
    without = slips(app, shop)["slips"][0]["rows"]
    assert [(r["priceCents"], r["continued"]) for r in without] == [(2000, False), (None, True)]


def _picking_with_two_wins(app, world):
    world["say"](
        app,
        [
            ("", "jess88 won the auction for Red bundle for $25.00", "player"),
            ("", "jess88 won the auction for Red bundle #2 for $25.00", "player"),
        ],
    )
    run(app, world["show"])
    with app.app_context():
        orders.advance(db.session.scalar(select(Order)), OrderStatus.picking, actor_label="t")
        db.session.commit()


def test_a_decline_after_picking_began_stays_on_the_reprinted_slip_as_a_marked_row(app, world):
    """A38: the packer may hold the old slip. A reprint says the lot was declined and must
    come out of the box, counts no item for it, and leaves once a person has dealt with it."""
    _picking_with_two_wins(app, world)
    with app.app_context():
        second = db.session.scalars(select(Sale).order_by(Sale.id)).all()[1]
        pipeline.set_sale_status(second, SaleStatus.payment_failed, actor="host", reason="declined")
        db.session.commit()
    doc = slips(app, world)
    [slip] = doc["slips"]
    assert slip["items"] == 1 and doc["totals"]["items"] == 1
    marked = [r for r in slip["rows"] if r.get("closed")]
    assert [(r["closed"], r["product"]) for r in marked] == [
        ("declined", "DECLINED, take it out: Red bundle")
    ]
    assert marked[0]["saleId"] is None and marked[0]["priceCents"] is None
    assert [r["product"] for r in slip["rows"] if not r.get("closed")] == ["Red bundle"]
    # A buyer whose only win was closed after picking still gets the marked slip, but counts as
    # no buyer and no item on the cover.
    with app.app_context():
        first = db.session.scalars(select(Sale).order_by(Sale.id)).first()
        pipeline.set_sale_status(first, SaleStatus.cancelled, actor="host", reason="cancelled")
        db.session.commit()
    doc = slips(app, world)
    [slip] = doc["slips"]
    assert slip["items"] == 0 and [r["closed"] for r in slip["rows"]] == ["cancelled", "declined"]
    assert doc["totals"] == {"buyers": 0, "items": 0, "gifts": 0} and doc["summary"] == []
    # Dealt with in Review: the marker goes.
    with app.app_context():
        for item in db.session.scalars(
            select(ReviewItem).where(ReviewItem.reason == "closed_after_picking")
        ):
            item.status = ReviewStatus.resolved
        db.session.commit()
    assert slips(app, world)["slips"] == []  # nothing left to say or to pack


def test_a_cancel_before_picking_began_just_leaves_the_slip(app, world):
    world["say"](
        app,
        [
            ("", "jess88 won the auction for Red bundle for $25.00", "player"),
            ("", "jess88 won the auction for Red bundle #2 for $25.00", "player"),
            ("host_shop", "cancelled for jess88"),
        ],
    )
    run(app, world["show"])
    doc = slips(app, world)
    assert not [r for b in doc["slips"] for r in b["rows"] if r.get("closed")]


def test_a_payment_failed_sale_is_on_neither_the_slips_nor_the_pick_list(app, world):
    """A61: a declined buyer's lot must not be packed. Two wins; one is declined before
    picking began: it is on no slip, no row asks to take it out (nothing was picked), and
    the per-product pick list counts only the standing one."""
    world["say"](
        app,
        [
            ("", "jess88 won the auction for Red bundle #1 for $25.00", "player"),
            ("", "kat.c won the auction for Red bundle #2 for $25.00", "player"),
            ("", "kat.c payment declined for Red bundle #2 for $25.00", "player"),
        ],
    )
    run(app, world["show"])
    with app.app_context():
        assert {s.buyer_username: s.status for s in db.session.scalars(select(Sale))} == {
            "jess88": SaleStatus.paid,
            "kat.c": SaleStatus.payment_failed,
        }
        pick = orders.pick_list(db.session.get(Show, world["show"]))
        db.session.rollback()
    assert [(p["product"], p["quantity"]) for p in pick] == [("Red bundle", 1)]
    doc = slips(app, world)
    assert [b["username"] for b in doc["slips"]] == ["jess88"]
    assert doc["totals"] == {"buyers": 1, "items": 1, "gifts": 0}
    assert not [r for b in doc["slips"] for r in b["rows"] if r.get("closed")]


# ---------- labels ----------


def test_a_package_whatnot_cancelled_or_replaced_is_never_printed(
    app,
    client,
    shop,
    sign_in,
    labels,  # noqa: F811
):
    """A14: Whatnot may keep a cancelled shipment's label links; the voided label must not be
    printed in the stack, counted as waiting, or offered one by one."""
    made = labelled(app, shop, {"buyer_a": 1, "buyer_b": 1, "buyer_c": 1})
    shop["fake"].set_shipment(made["buyer_a"]["id"], status="cancelled_not_shipped")
    shop["fake"].set_shipment(made["buyer_b"]["id"], replacement_shipment_id=made["buyer_c"]["id"])
    read_shipments(app, shop)
    v = view(app, shop)
    packs = {b["buyer"]: b["packages"][0] for b in v["buyers"]}
    assert [packs[b]["labelReady"] for b in ("buyer_a", "buyer_b", "buyer_c")] == [
        False,
        False,
        True,
    ]
    assert v["labelsToPrint"] == 1

    h = sign_in(shop["org"], role="fulfillment")
    r = press(client, shop, h)
    assert r.status_code == 200 and counts_of(r) == (1, 0, 0)
    assert labels["fetched"] == [f"https://labels.example/{made['buyer_c']['id']}-bundle.pdf"]
    # One by one: a cancelled package is refused in words, and nothing is fetched or marked.
    with app.app_context():
        gone_id = db.session.scalar(
            select(WhatnotShipment.id).where(WhatnotShipment.whatnot_id == made["buyer_a"]["id"])
        )
    labels["fetched"].clear()
    r = client.get(f"/api/shipping/shipments/{gone_id}/label.pdf")
    assert r.status_code == 409 and "cancelled" in r.get_json()["error"]
    assert labels["fetched"] == []
    with app.app_context():
        assert db.session.get(WhatnotShipment, gone_id).label_printed_at is None


def counts_of(response) -> tuple[int, int, int]:
    h = response.headers
    return int(h["X-Labels-Printed"]), int(h["X-Labels-Failed"]), int(h["X-Labels-Left"])


def ready(app, shop, buyer="lani808"):
    p = parcel(shop, buyer, [wo.order(shop, buyer, "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    return p


def test_the_label_job_looks_again_at_the_buyers_packages_before_it_asks(app, shop):
    """A45: the job runs later than the press. A second open package for the buyer, which
    appeared meanwhile, means two labels unless they are combined first: nothing is sent."""
    from types import SimpleNamespace

    ready(app, shop)
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        assert shipping.request_labels(show, user=SimpleNamespace(id=None)) == {"requested": 1}
        db.session.commit()
        payload = db.session.scalar(select(Job).where(Job.kind == "whatnot_labels")).payload
    assert payload["show_id"] == shop["show"]
    # Meanwhile the buyer wins again and Whatnot opens a second package.
    parcel(shop, "lani808", [wo.order(shop, "lani808", "Blue bundle")])
    read(app, shop)
    read_shipments(app, shop)
    with app.app_context():
        shipping.labels_job(payload)
    assert shop["fake"].labels_requested == []
    # A job queued before the show was part of it sends nothing: a label cannot be undone.
    with app.app_context():
        shipping.labels_job({k: v for k, v in payload.items() if k != "show_id"})
    assert shop["fake"].labels_requested == []


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_a_409_that_is_not_a_plain_refusal_is_waited_out_not_asked_again(
    app, client, shop, sign_in
):
    """A45: only Whatnot's "label-generation-rejected" says nothing was started. Any other 409
    may be a label already being made: it is held like a request that may have gone through."""
    ready(app, shop)
    ready(app, shop, "kat.c")
    h = sign_in(shop["org"], role="manager")
    # The job asks in A-Z order: kat.c first.
    shop["fake"].fail_next(409, "label-generation-rejected")
    shop["fake"].fail_next(409, "label-in-progress")
    out = client.post(
        f"/api/shipping/shows/{shop['show']}/labels", json={"confirm": True}, headers=h
    ).get_json()
    got = {b["buyer"]: b["packages"][0] for b in out["buyers"]}
    assert (got["kat.c"]["labelProblem"], got["kat.c"]["askable"]) == ("not_now", True)
    assert (got["lani808"]["labelProblem"], got["lani808"]["askable"]) == ("unknown", False)


def renamed(shop, a="old_name", b="new_name"):
    """One Whatnot account that changed its username between two wins."""
    make = shop["fake"].add_order
    kw = {"show_id": shop["wshow"], "buyer_id": "u-77"}
    first = make(buyer=a, items=[{"title": "Red bundle", "price_cents": 2500}], **kw)
    second = make(buyer=b, items=[{"title": "Blue bundle", "price_cents": 900}], **kw)
    return first, second


def test_a_buyer_who_changed_username_mid_show_is_one_buyer_with_one_label(app, shop):
    """A44: grouped by Whatnot's own buyer id, not by the name text: one account that was
    called two things is one row, one package and one label."""
    first, second = renamed(shop)
    parcel(shop, "new_name", [first, second])
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert [(b["buyer"], b["items"], len(b["packages"])) for b in v["buyers"]] == [
        ("new_name", 2, 1)
    ]
    assert v["totals"]["buyers"] == 1 and v["labelsToMake"] == 1
    with app.app_context():
        from types import SimpleNamespace

        out = shipping.request_labels(
            db.session.get(Show, shop["show"]), user=SimpleNamespace(id=None)
        )
        db.session.commit()
        ids = db.session.scalar(select(Job).where(Job.kind == "whatnot_labels")).payload
    assert out == {"requested": 1} and len(ids["shipment_ids"]) == 1


def test_a_renamed_buyer_in_two_open_packages_is_called_out_as_split(app, shop):
    first, second = renamed(shop)
    parcel(shop, "old_name", [first])
    parcel(shop, "new_name", [second])
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert [(b["buyer"], b["state"]) for b in v["buyers"]] == [("new_name", "split")]
    assert v["labelsToMake"] == 0


def test_an_order_cancelled_after_its_package_was_labelled_is_flagged(app, shop):
    """A41: a labelled package's items are not read again, so a cancelled order's item stays
    in it. The label still lists it: the package says so, and a package of only cancelled
    items is no label to print."""
    a = wo.order(shop, "buyer_a", "Red bundle")
    b = wo.order(shop, "buyer_a", "Blue bundle", 900)
    only = wo.order(shop, "buyer_b", "Red bundle")
    parcel(shop, "buyer_a", [a, b], status="label_generated", tracking="94")
    parcel(shop, "buyer_b", [only], status="label_generated", tracking="95")
    read(app, shop)
    read_shipments(app, shop)
    assert [p["cancelledItems"] for x in view(app, shop)["buyers"] for p in x["packages"]] == [0, 0]
    shop["fake"].set_order_status(b["id"], "cancelled")
    shop["fake"].set_order_status(only["id"], "cancelled")
    read(app, shop)
    v = view(app, shop)
    by = {x["buyer"]: x for x in v["buyers"]}
    # buyer_a keeps the item that still ships, and the package says one was cancelled.
    [pack] = by["buyer_a"]["packages"]
    assert (by["buyer_a"]["items"], pack["items"], pack["cancelledItems"]) == (1, 1, 1)
    assert pack["labelReady"] is True
    # buyer_b has nothing left that ships, yet is not lost: the label is called out, and not
    # offered to print.
    [gone] = by["buyer_b"]["packages"]
    assert (by["buyer_b"]["items"], gone["items"], gone["cancelledItems"]) == (0, 0, 1)
    assert gone["labelReady"] is False and v["labelsToPrint"] == 1


def test_a_declined_card_whose_retry_paid_is_not_called_cancelled(app, shop):
    """A41: only a cancelled order is flagged. A failed one is often a declined card retried
    and paid as another order; its item is not one to take out of the box."""
    a = wo.order(shop, "buyer_a", "Red bundle")
    declined = wo.order(shop, "buyer_a", "Red bundle", status="failed")
    parcel(shop, "buyer_a", [a, declined], status="label_generated", tracking="94")
    read(app, shop)
    read_shipments(app, shop)
    [pack] = view(app, shop)["buyers"][0]["packages"]
    assert pack["cancelledItems"] == 0 and pack["labelReady"] is True


def test_a_cancelled_item_in_a_labelled_package_is_not_also_another_shows_item(app, shop):
    """A41 (review): the cancelled item is counted once, as cancelled. The package must not
    also say it "holds an item from another show: pack them together"."""
    a = wo.order(shop, "buyer_a", "Red bundle")
    b = wo.order(shop, "buyer_a", "Blue bundle", 900)
    parcel(shop, "buyer_a", [a, b], status="label_generated", tracking="94")
    read(app, shop)
    read_shipments(app, shop)
    shop["fake"].set_order_status(b["id"], "cancelled")
    read(app, shop)
    [pack] = view(app, shop)["buyers"][0]["packages"]
    assert (pack["items"], pack["cancelledItems"], pack["otherItems"]) == (1, 1, 0)


def test_a_shared_package_whose_only_item_here_was_cancelled_still_ships(app, shop):
    """A41 (review): the parcel also holds a paid win of an ended earlier show. This show's
    item is cancelled: the package must not be called "do not ship" and keeps its label."""
    other = shop["fake"].add_show(
        title="Thursday",
        start=shop["now"] - timedelta(days=1, hours=2),
        ended=shop["now"] - timedelta(days=1),
    )
    earlier = shop["fake"].add_order(
        show_id=other["id"],
        buyer="lani808",
        items=[{"title": "Red bundle", "price_cents": 2500}],
        created=shop["now"] - timedelta(days=1, hours=1),
    )
    mine = wo.order(shop, "lani808", "Blue bundle", 900)
    parcel(shop, "lani808", [earlier, mine], status="label_generated", tracking="94")
    read(app, shop)
    read_shipments(app, shop)
    shop["fake"].set_order_status(mine["id"], "cancelled")
    read(app, shop)
    [pack] = view(app, shop)["buyers"][0]["packages"]
    # Nothing of this show ships in it, one item of the other show does: the page says "do not
    # ship" only when both are zero.
    assert (pack["items"], pack["cancelledItems"], pack["otherItems"]) == (0, 1, 1)
    assert pack["labelReady"] is True


def _picking_with_lots(app, shop):
    o1 = wo.order(shop, "jess88", "Red bundle")
    o2 = wo.order(shop, "jess88", "Red bundle", 2600)
    parcel(shop, "jess88", [o1, o2])
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    with app.app_context():
        orders.advance(db.session.scalar(select(Order)), OrderStatus.picking, actor_label="t")
        db.session.commit()
    return o1, o2


def test_a_decline_after_picking_with_the_whatnot_lots_read_is_one_row_not_two(app, shop):
    """A38 (review): Whatnot's order still stands, so its lot is stored. The declined win is
    the marked "take it out" row; its lot must not also be a row to pick."""
    _picking_with_lots(app, shop)
    with app.app_context():
        second = db.session.scalars(select(Sale).order_by(Sale.id)).all()[1]
        pipeline.set_sale_status(second, SaleStatus.payment_failed, actor="host", reason="declined")
        db.session.commit()
    doc = slips(app, shop)
    [slip] = doc["slips"]
    rows = [(r["product"], r.get("closed")) for r in slip["rows"]]
    assert rows == [("Red bundle", None), ("DECLINED, take it out: Red bundle", "declined")]
    assert slip["items"] == 1 and doc["totals"]["items"] == 1


def test_a_declined_win_then_cancelled_keeps_its_take_it_out_marker(app, world):
    """A38 (review): after picking, a win is declined and then cancelled (Whatnot cancels the
    failed order). Nobody has dealt with the unit in the box: the Review note stands, and so
    does the marker, until a person resolves it."""
    _picking_with_two_wins(app, world)
    with app.app_context():
        second = db.session.scalars(select(Sale).order_by(Sale.id)).all()[1]
        pipeline.set_sale_status(second, SaleStatus.payment_failed, actor="host", reason="declined")
        db.session.commit()
        second = db.session.scalars(select(Sale).order_by(Sale.id)).all()[1]
        pipeline.set_sale_status(second, SaleStatus.cancelled, actor="whatnot", reason="cancelled")
        db.session.commit()
        notes = db.session.scalars(
            select(ReviewItem).where(ReviewItem.reason == "closed_after_picking")
        ).all()
        assert [n.status for n in notes] == [ReviewStatus.open]
    marks = [r for r in slips(app, world)["slips"][0]["rows"] if r.get("closed")]
    assert [r["closed"] for r in marks] == ["cancelled"]


def test_a_retried_order_for_the_same_lot_is_still_a_row_to_pick(app, shop):
    """A38 (review): the declined card is retried and paid as a new Whatnot order of the same
    title, whose lot is read before the app has applied it. The closed sale must not take that
    lot as its own: the new lot is still one to pick, next to the "take it out" row."""
    first = wo.order(shop, "jess88", "Red bundle #2")
    parcel(shop, "jess88", [first])
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    with app.app_context():
        orders.advance(db.session.scalar(select(Order)), OrderStatus.picking, actor_label="t")
        sale = db.session.scalar(select(Sale))
        pipeline.set_sale_status(sale, SaleStatus.payment_failed, actor="host", reason="declined")
        db.session.commit()
    shop["fake"].set_order_status(first["id"], "failed")
    retry = wo.order(shop, "jess88", "Red bundle #2")
    parcel(shop, "jess88", [retry])
    read(app, shop)
    read_shipments(app, shop)
    [slip] = slips(app, shop)["slips"]
    rows = [(r.get("closed"), r["whatnotOrder"]) for r in slip["rows"]]
    assert (None, retry["id"]) in rows, rows
    assert [c for c, _ in rows if c] == ["declined"]

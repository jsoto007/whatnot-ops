"""A win Whatnot says is paid while its product is still to be named (a "CHOOSE ANY PRODUCT"
lot: the winner picks in chat). The sale keeps waiting for its product (it holds no stock
and is not "paid" here until a product is named), but it must not look unpaid or vanish:

- the show's tiles count Whatnot's paid money for it as "paid, product to pick";
- the order and the pick list say which wins are paid and still to pick;
- a named, paid item is on the pick list even while another win of the buyer waits;
- "Ignore" cannot leave such a win with no open question, and one left that way gets its
  question back at the next orders read;
- "name it once" is never offered for a lot whose title says the winner chooses;
- no AI call is spent on such a lot when Whatnot's order made the sale;
- a win made before the laptop recorded any chat reads "chat not recorded", not "did not
  pick";
- an order named after its parcel left is marked shipped at the next shipments pass.

Every buyer, lot and order here is made up; Whatnot is the in-memory fake.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.db import db
from app.models import (
    Job,
    Order,
    OrderStatus,
    ReviewItem,
    ReviewStatus,
    Sale,
    Show,
    WhatnotShipment,
    WhatnotVariant,
)
from app.services import activity, dashboard, orders, pipeline, supervisor
from app.services.inventory import Actor
from app.services.whatnot import shipping
from tests import test_pick_worker as pw
from tests import test_whatnot_catalog as cat
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import apply, order, read, set_mode, win

shop = wo.shop  # a connected shop; its show is matched to Whatnot by the first read
stage = pw.stage  # a winner's-pick show that ended twenty minutes ago (the pick worker's)
found = pw.found

CHOOSE = "CHOOSE ANY PRODUCT YOU WANT #{}"


def tiles(app, shop):
    with app.app_context():
        return dashboard.show_tiles(db.session.get(Show, shop["show"]))


def unmatched(app, sale_id=None):
    with app.app_context():
        q = select(ReviewItem).where(ReviewItem.reason == "unmatched_item")
        if sale_id is not None:
            q = q.where(ReviewItem.sale_id == sale_id)
        return [
            (i.id, i.sale_id, i.status.value) for i in db.session.scalars(q.order_by(ReviewItem.id))
        ]


# ---------- the money is shown, the sale still waits ----------


def test_a_paid_choose_any_win_counts_as_paid_with_a_product_to_pick(app, shop):
    order(shop, "made_up_ann", CHOOSE.format(12), 1100)
    pending = order(shop, "made_up_bo", CHOOSE.format(13), 900)
    shop["fake"].set_order_status(pending["id"], "pending")  # not paid on Whatnot yet
    read(app, shop)
    apply(app, shop)
    t = tiles(app, shop)
    # The sale still waits for its product (no stock held, not "paid" here) ...
    assert (t["revenuePaidCents"], t["revenuePendingCents"]) == (0, 0)
    # ... but Whatnot's payment is shown: one win, paid, product to pick.
    assert (t["toPick"], t["revenueToPickCents"]) == (1, 1100)
    # "Sales so far" (the live band) is everything sold: paid, awaiting, and to pick. It is
    # added up here, never on the screen; "paid" itself stays what is paid here.
    assert t["revenueSoldCents"] == 1100
    with app.app_context():
        assert {s.buyer_username: s.status.value for s in db.session.scalars(select(Sale))} == {
            "made_up_ann": "detected",
            "made_up_bo": "detected",
        }


def test_the_order_and_its_sale_say_paid_on_whatnot_product_to_pick(app, client, shop, sign_in):
    order(shop, "made_up_ann", CHOOSE.format(12), 1100)
    read(app, shop)
    apply(app, shop)
    h = sign_in(shop["org"], role="manager")
    rows = client.get("/api/orders", headers=h).get_json()["orders"]
    assert [(o["buyer"], o["status"], o["toPick"]) for o in rows] == [("made_up_ann", "open", 1)]
    sale = rows[0]["sales"][0]
    assert (sale["toPick"], sale["choiceTitle"]) == (True, True)
    one = client.get(f"/api/orders/{rows[0]['id']}", headers=h).get_json()["order"]
    assert one["toPick"] == 1


def test_the_pick_list_lists_wins_to_pick_and_keeps_named_paid_items(app, client, shop, sign_in):
    order(shop, "made_up_cy", "Red bundle", 2500)  # names a product: paid at once
    order(shop, "made_up_cy", CHOOSE.format(14), 1100)  # waits for the winner's pick
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        assert db.session.scalar(select(Order)).status.value == "open"
    h = sign_in(shop["org"], role="fulfillment")
    body = client.get(f"/api/orders/shows/{shop['show']}/pick-list", headers=h).get_json()
    # The buyer's named, paid item is pulled even though their other win still waits.
    assert [(i["code"], i["quantity"], i["orders"]) for i in body["items"]] == [("RED", 1, 1)]
    assert [(w["buyer"], w["lot"], w["priceCents"]) for w in body["toPick"]] == [
        ("made_up_cy", "Listing 14", 1100)
    ]


def test_picking_such_an_order_is_refused_saying_the_product_is_to_pick(app, client, shop, sign_in):
    """Picking stays refused (nothing is held for a win until its product is named), but the
    reason says what is missing: the winner's pick, not a payment."""
    order(shop, "made_up_cy", CHOOSE.format(14), 1100)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        order_id = db.session.scalar(select(Order.id))
    h = sign_in(shop["org"], role="fulfillment")
    r = client.post(f"/api/orders/{order_id}/status", json={"status": "picking"}, headers=h)
    assert r.status_code == 409
    assert "paid on Whatnot" in r.get_json()["error"]
    with app.app_context():
        assert db.session.scalar(select(Order.status)).value == "open"


# ---------- "Ignore" never strands a win ----------


def test_ignore_is_refused_for_a_win_still_waiting_for_its_product(app, client, shop, sign_in):
    order(shop, "made_up_di", CHOOSE.format(15), 1100)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    h = sign_in(shop["org"], role="manager")
    # The screen hides "Ignore" for it (it reads `toPick`, the same rule the server keeps).
    listed = client.get("/api/review", headers=h).get_json()["items"]
    assert [(i["id"], i["sale"]["toPick"]) for i in listed] == [(item_id, True)]
    r = client.post(f"/api/review/{item_id}/resolve", json={"action": "dismiss"}, headers=h)
    assert r.status_code == 409
    error = r.get_json()["error"]
    # A "choose any" lot has one way out: the winner's pick ("Not one of ours" would not do).
    assert "Name the product the winner picked" in error and "Not one of ours" not in error
    assert unmatched(app) == [(item_id, sale_id, "open")]


def test_a_paid_win_of_an_unknown_title_says_both_ways_out(app, client, shop, sign_in):
    """A title that names no product of the shop: name it, or mark its listing "Not one of
    ours" (then it may be ignored, as the next test shows)."""
    order(shop, "made_up_ed", "Mystery widget deluxe", 900)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    h = sign_in(shop["org"], role="manager")
    r = client.post(f"/api/review/{item_id}/resolve", json={"action": "dismiss"}, headers=h)
    assert r.status_code == 409
    error = r.get_json()["error"]
    assert "Name the product" in error and '"Not one of ours"' in error
    assert unmatched(app) == [(item_id, sale_id, "open")]


def test_a_chat_made_win_whatnot_has_not_said_is_paid_can_still_be_ignored(
    app, client, shop, sign_in
):
    """Only a win Whatnot's own order says is paid must be named. A win read from chat alone
    (Compare only, not connected, or before the orders are applied) keeps "Ignore", as
    before: the screen never swaps it for cancelling what may be a real sale."""
    shop["say"](app, [win("made_up_ed", "Mystery widget deluxe", "9.00")])
    run(app, shop["show"])
    [(item_id, sale_id, _)] = unmatched(app)
    h = sign_in(shop["org"], role="manager")
    listed = client.get("/api/review", headers=h).get_json()["items"]
    assert [(i["id"], i["sale"]["toPick"]) for i in listed] == [(item_id, False)]
    r = client.post(f"/api/review/{item_id}/resolve", json={"action": "dismiss"}, headers=h)
    assert r.status_code == 200, r.get_json()
    assert unmatched(app) == [(item_id, sale_id, "dismissed")]
    with app.app_context():
        assert db.session.get(Sale, sale_id).status.value == "detected"  # nothing cancelled
    assert level(app, shop["red"]) == (3, 0)


def test_a_paid_win_on_a_listing_marked_none_of_ours_can_be_ignored_and_stays_ignored(
    app, client, shop, sign_in
):
    """A person said the Whatnot listing is none of the shop's products: its paid win is not
    a product to pick. "Ignore" works, the next orders read does not ask again, and the
    tiles do not count it as paid with a product to pick."""
    product = shop["fake"].add_product(title="Red bundle", variants=[{"sku": "RED"}])
    cat.sync(app, shop)
    variant = shop["fake"].variants_of(product["id"])[0]
    h = sign_in(shop["org"])
    r = client.patch(
        f"/api/whatnot/catalog/{cat.variant_id(app, 'RED')}/link", json={"skuId": None}, headers=h
    )
    assert r.status_code == 200
    cat.order_for(shop, "made_up_zed", variant, title="Red bundle", price=1500)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    with app.app_context():
        sale = db.session.get(Sale, sale_id)
        assert sale.external_order_id and (sale.status.value, sale.sku_id) == ("detected", None)
        assert orders.to_pick_ids([sale_id]) == set()
    t = tiles(app, shop)
    assert (t["toPick"], t["revenueToPickCents"]) == (0, 0)
    listed = client.get("/api/review", headers=h).get_json()["items"]
    assert [i["sale"]["toPick"] for i in listed] == [False]
    r = client.post(f"/api/review/{item_id}/resolve", json={"action": "dismiss"}, headers=h)
    assert r.status_code == 200, r.get_json()
    apply(app, shop)  # the next read does not reopen it
    assert unmatched(app) == [(item_id, sale_id, "dismissed")]


def test_a_choose_any_win_stays_to_pick_even_if_its_listing_is_marked_not_one_of_ours(
    app, client, shop, sign_in
):
    """A "CHOOSE ANY" listing is a lot, not a product: the winner picks from the shop's own
    shelves. The listings page lists it as "Not linked yet", and marking it "Not one of
    ours" only says it is no single product, which was true already. Its paid wins still
    wait for their pick: counted, listed, never ignored."""
    title = CHOOSE.format(60)
    product = shop["fake"].add_product(title=title, variants=[{}])
    cat.sync(app, shop)
    variant = shop["fake"].variants_of(product["id"])[0]
    with app.app_context():
        listing = db.session.scalar(
            select(WhatnotVariant.id).where(WhatnotVariant.whatnot_id == variant["id"])
        )
    h = sign_in(shop["org"])
    r = client.patch(f"/api/whatnot/catalog/{listing}/link", json={"skuId": None}, headers=h)
    assert r.status_code == 200
    shop["fake"].add_order(
        show_id=shop["wshow"],
        buyer="made_up_kai",
        items=[{"title": title, "price_cents": 1100, "variant_id": variant["id"]}],
        created=datetime.now(UTC) - timedelta(seconds=20),
    )
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    t = tiles(app, shop)
    assert (t["toPick"], t["revenueToPickCents"]) == (1, 1100)
    listed = client.get("/api/review", headers=h).get_json()["items"]
    assert [i["sale"]["toPick"] for i in listed] == [True]
    r = client.post(f"/api/review/{item_id}/resolve", json={"action": "dismiss"}, headers=h)
    assert r.status_code == 409
    assert "Not one of ours" not in r.get_json()["error"]  # no way out that does not work
    assert unmatched(app) == [(item_id, sale_id, "open")]


def test_an_ai_dismiss_suggestion_cannot_ignore_a_win_to_pick_either(app, shop):
    order(shop, "made_up_di", CHOOSE.format(16), 1100)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    with app.app_context():
        item = db.session.get(ReviewItem, item_id)
        item.proposal = {"action": "dismiss", "confidence": 0.99}
        with pytest.raises(pipeline.ReviewError):
            supervisor.apply_proposal(item, actor=Actor("ai", None, "AI"))
        db.session.rollback()
    assert unmatched(app) == [(item_id, sale_id, "open")]


def test_a_win_whose_question_was_ignored_before_gets_it_back_at_the_next_read(app, shop):
    order(shop, "made_up_di", CHOOSE.format(15), 1100)
    read(app, shop)
    apply(app, shop)
    [(item_id, sale_id, _)] = unmatched(app)
    with app.app_context():  # what an "Ignore" before this change left behind
        db.session.execute(
            update(ReviewItem).where(ReviewItem.id == item_id).values(status=ReviewStatus.dismissed)
        )
        db.session.commit()
    apply(app, shop)
    assert [(s, st) for _, s, st in unmatched(app)] == [(sale_id, "dismissed"), (sale_id, "open")]
    apply(app, shop)  # and only once
    assert len(unmatched(app)) == 2


# ---------- "name it once" is never for a lot the winner chooses ----------


def test_name_it_once_is_refused_for_a_lot_whose_title_says_the_winner_chooses(
    app, client, shop, sign_in
):
    for n, buyer in enumerate(("made_up_ann", "made_up_bo", "made_up_cy"), start=20):
        order(shop, buyer, CHOOSE.format(n), 1100)
    read(app, shop)
    apply(app, shop)
    items = unmatched(app)
    h = sign_in(shop["org"], role="manager")
    for extra in ({"applyToSimilar": True}, {"applyTo": [items[1][0], items[2][0]]}):
        r = client.post(
            f"/api/review/{items[0][0]}/resolve",
            json={"action": "assign_sku", "skuId": shop["red"], **extra},
            headers=h,
        )
        assert r.status_code == 400, r.get_json()
    assert [st for *_, st in unmatched(app)] == ["open", "open", "open"]  # nothing named
    # Naming the one lot on its own still works.
    r = client.post(
        f"/api/review/{items[0][0]}/resolve",
        json={"action": "assign_sku", "skuId": shop["red"]},
        headers=h,
    )
    assert r.status_code == 200 and r.get_json()["alsoResolved"] == 0
    assert [st for *_, st in unmatched(app)] == ["resolved", "open", "open"]


# ---------- no AI call for an order-made "choose any" win ----------


def test_an_order_made_choose_any_win_asks_no_ai_which_product(app, shop, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")
    order(shop, "made_up_ann", CHOOSE.format(30), 1100)
    order(shop, "made_up_bo", "Mystery widget deluxe", 900)
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        asked = [
            db.session.get(ReviewItem, j.payload["item_id"]).sale.buyer_username
            for j in db.session.scalars(select(Job).where(Job.kind == "ai_review_item"))
        ]
    assert asked == ["made_up_bo"]  # "Mystery widget deluxe" may still name a product


# ---------- "chat not recorded" is not "did not pick" ----------


def test_a_win_made_before_any_chat_was_recorded_is_not_read_as_did_not_pick(app, stage):
    stage["win"]("made_up_ann", 1, minute=-3)  # the laptop started recording at minute 0
    stage["win"]("made_up_bo", 2, minute=4)  # recorded, and the winner typed nothing
    assert stage["go"]() == {"not_recorded": 1, "none": 1, "changed": 2}
    assert found(app) == [{"state": "not_recorded"}, {"state": "none"}]
    # The activity log says it in words, as for every other finding.
    assert activity.describe("picks.read", {"not_recorded": 1, "none": 1, "changed": 2}) == (
        "Read the winners' picks after a show: 1 did not pick, "
        "1 not read because no laptop was recording the chat yet when they won"
    )


def test_a_win_seconds_before_the_first_recorded_line_is_still_did_not_pick(app, stage):
    """Whatnot stamps its order some seconds after the hammer, and a laptop counts as in step
    up to 15 seconds out: a win a few seconds before the first line was recorded is a win
    the laptop saw the chat for, so a winner who typed nothing did not pick."""
    stage["win"]("made_up_ann", 1, minute=-5 / 60)  # five seconds before the first line
    assert stage["go"]() == {"none": 1, "changed": 1}
    assert found(app) == [{"state": "none"}]


# ---------- shipped once the last product is named ----------
# A win Whatnot's order says is paid leaves with its parcel, named or not (Javier's call,
# 2026-11-21: tests/test_carrier_ships_open_orders.py). What still holds an order back is a
# win only chat saw: Whatnot has no order for it, so it is in no parcel.


def _a_win_only_chat_saw(app, shop, n):
    shop["say"](app, [win("made_up_ann", CHOOSE.format(n), "5.00")])
    run(app, shop["show"])


def test_an_order_named_after_its_parcel_left_is_shipped_at_the_next_pass(app, shop):
    set_mode(app, shop, "auto")
    _a_win_only_chat_saw(app, shop, 40)
    o = order(shop, "made_up_ann", "Red bundle", 2500)
    shop["fake"].add_shipment(
        recipient="made_up_ann",
        order_ids=[o["id"]],
        status="dropped_off",
        tracking="9400MADEUP",
        created=shop["now"] - timedelta(minutes=50),
    )
    read(app, shop)
    apply(app, shop)

    def shipments_pass():
        with app.app_context():
            shipping.sync_shipments_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
            db.session.commit()

    def status():
        with app.app_context():
            return db.session.scalar(select(Order)).status.value

    shipments_pass()
    assert status() == "open"  # the win only chat saw is not named, and in no parcel
    with app.app_context():
        # The parcel has not changed since: nothing tells the next pass about this show.
        db.session.execute(
            update(WhatnotShipment).values(updated_at=datetime.now(UTC) - timedelta(hours=2))
        )
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "unmatched_item"))
        pipeline.resolve(
            item, "assign_sku", {"skuId": shop["red"]}, user=None, actor=Actor("user", None, "M")
        )
        db.session.commit()
        assert db.session.scalar(select(Order)).status.value == "paid"
    shipments_pass()
    assert status() == "shipped"


def test_an_order_in_the_shows_second_recording_is_shipped_once_named(app, shop):
    """A show recorded twice (a laptop started late) keeps its parcels on the first
    recording, while a buyer's order may live in the second. Every other order of the first
    recording is already shipped, so only the buyer's own order, in either recording, says
    the show needs another look."""
    set_mode(app, shop, "auto")
    _a_win_only_chat_saw(app, shop, 41)
    o = order(shop, "made_up_ann", "Red bundle", 2500)
    order(shop, "made_up_bo", "Red bundle", 2500)  # named, paid, and shipped below
    shop["fake"].add_shipment(
        recipient="made_up_ann",
        order_ids=[o["id"]],
        status="dropped_off",
        tracking="9400MADEUP2",
        created=shop["now"] - timedelta(minutes=50),
    )
    read(app, shop)
    apply(app, shop)

    def shipments_pass():
        with app.app_context():
            shipping.sync_shipments_job({"org_id": shop["org"], "whatnot_show_id": shop["wshow"]})
            db.session.commit()

    shipments_pass()
    with app.app_context():
        first = db.session.get(Show, shop["show"])
        second = Show(
            organization_id=first.organization_id,
            show_key="2026-11-01T20:05:00.000Z",
            started_at=first.started_at,
            show_date=first.show_date,
            whatnot_show_id=first.whatnot_show_id,
        )
        db.session.add(second)
        db.session.flush()
        ann = db.session.scalar(select(Order).where(Order.buyer_username == "made_up_ann"))
        # The buyer's order is the second recording's; the first keeps the other buyer's.
        ann.show_id = second.id
        db.session.execute(update(Sale).where(Sale.order_id == ann.id).values(show_id=second.id))
        db.session.execute(
            update(Order)
            .where(Order.buyer_username == "made_up_bo")
            .values(status=OrderStatus.shipped)
        )
        db.session.execute(
            update(WhatnotShipment).values(
                show_id=first.id, updated_at=datetime.now(UTC) - timedelta(hours=2)
            )
        )
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.reason == "unmatched_item"))
        pipeline.resolve(
            item, "assign_sku", {"skuId": shop["red"]}, user=None, actor=Actor("user", None, "M")
        )
        db.session.commit()
        assert db.session.get(Order, ann.id).status.value == "paid"
        ann_id = ann.id
    shipments_pass()
    with app.app_context():
        assert db.session.get(Order, ann_id).status.value == "shipped"


def test_orders_helper_finds_only_paid_unnamed_wins(app, shop):
    named = order(shop, "made_up_ann", "Red bundle", 2500)
    order(shop, "made_up_bo", CHOOSE.format(50), 1100)
    failed = order(shop, "made_up_cy", CHOOSE.format(51), 1100)
    shop["fake"].set_order_status(failed["id"], "failed")
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        ids = {s.buyer_username: s.id for s in db.session.scalars(select(Sale))}
        assert orders.to_pick_ids(list(ids.values())) == {ids["made_up_bo"]}
    assert named["id"]

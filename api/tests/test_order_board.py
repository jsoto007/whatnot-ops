"""The Orders page by show (`services/order_board.py`): every buyer of a show in one tab, all
their wins together, Whatnot's giveaways beside them, a manager's "Mark ready to pack". Runs
against the in-memory fake Whatnot; buyer names are made up."""

from datetime import timedelta

from sqlalchemy import select

from app.db import db
from app.models import AuditLog, Order, OrderStatus, ReviewItem, Sale, Show
from app.services import buyers, order_board
from tests import test_whatnot_orders as wo
from tests.test_giveaways import give
from tests.test_whatnot_orders import apply, order, read

shop = wo.shop
CHOICE = "CHOOSE ANY PRODUCT YOU WANT #4"


def board(client, shop):
    r = client.get(f"/api/orders/shows/{shop['show']}/board")
    assert r.status_code == 200, r.get_json()
    return {b["username"]: b for b in r.get_json()["buyers"]}, r.get_json()["counts"]


def tabs(client, shop):
    return {name: b["tab"] for name, b in board(client, shop)[0].items()}


def name_giveaway(client, shop, h, buyer):
    (g,) = [x for x in client.get("/api/giveaways").get_json()["giveaways"] if x["buyer"] == buyer]
    r = client.put(f"/api/giveaways/{g['id']}/product", json={"skuId": shop["red"]}, headers=h)
    assert r.status_code == 200, r.get_json()


def test_each_buyer_is_in_one_tab_with_every_win_and_giveaway_together(app, client, shop, sign_in):
    order(shop, "made_up_paid", "Red bundle")  # paid, product known: ready to pack
    order(shop, "made_up_picks", CHOICE)  # paid, the winner still picks: needs review
    order(shop, "made_up_both", "Red bundle")
    give(shop, buyer="made_up_both")  # bought and won a giveaway with no product yet
    give(shop, buyer="made_up_gift_only")  # won a giveaway, bought nothing
    read(app, shop)
    apply(app, shop)
    sign_in(shop["org"], "manager")
    found, counts = board(client, shop)
    assert {n: b["tab"] for n, b in found.items()} == {
        "made_up_paid": "pack",
        "made_up_picks": "review",
        "made_up_both": "review",  # the giveaway holds the whole box
        "made_up_gift_only": "review",
    }
    assert counts["review"] == 3 and counts["pack"] == 1 and counts["shipped"] == 0
    both = found["made_up_both"]
    assert both["order"]["buyer"] == "made_up_both" and not both["giveawayOnly"]
    assert [g["product"] for g in both["giveaways"]] == [None]
    only = found["made_up_gift_only"]
    assert only["giveawayOnly"] and only["order"] is None and len(only["giveaways"]) == 1
    # A giveaway is never a sale, whatever tab its buyer is in.
    with app.app_context():
        assert (
            db.session.scalar(select(Sale).where(Sale.buyer_username == "made_up_gift_only"))
            is None
        )


def test_naming_a_giveaway_moves_its_buyer_to_ready_to_pack_as_an_order(app, client, shop, sign_in):
    order(shop, "made_up_both", "Red bundle")
    give(shop, buyer="made_up_both")
    give(shop, buyer="made_up_gift_only")
    read(app, shop)
    apply(app, shop)
    h = sign_in(shop["org"], "manager")
    name_giveaway(client, shop, h, "made_up_both")
    name_giveaway(client, shop, h, "made_up_gift_only")
    assert tabs(client, shop) == {"made_up_both": "pack", "made_up_gift_only": "pack"}
    # The Ready to pack printout holds both, the gift-only buyer included; one buyer prints alone.
    r = client.get(f"/api/orders/shows/{shop['show']}/order-slips?only=pack")
    assert r.status_code == 200, r.get_json()
    slips = r.get_json()
    assert sorted(s["username"] for s in slips["slips"]) == ["made_up_both", "made_up_gift_only"]
    one = client.get(f"/api/orders/shows/{shop['show']}/order-slips?buyer=@Made_Up_Both").get_json()
    assert [s["username"] for s in one["slips"]] == ["made_up_both"] and one["leftOut"] == 1


def test_a_cancelled_order_waits_in_review_and_is_counted_apart(app, client, shop, sign_in):
    o = order(shop, "made_up_cancel", "Red bundle")
    read(app, shop)
    apply(app, shop)
    shop["fake"].set_order_status(o["id"], "cancelled")
    read(app, shop)
    apply(app, shop)
    sign_in(shop["org"], "manager")
    found, counts = board(client, shop)
    b = found["made_up_cancel"]
    assert b["tab"] == "review" and b["cancelled"] and not b["mayMarkReady"]
    assert counts["cancelled"] == 1
    # Nothing of theirs goes in a box, so it can never be marked ready to pack.
    h = sign_in(shop["org"], "manager")
    r = client.post(
        f"/api/orders/shows/{shop['show']}/ready", json={"buyer": "made_up_cancel"}, headers=h
    )
    assert r.status_code == 409


def test_a_shipped_box_is_done_even_with_a_giveaway_still_unnamed(app, client, shop, sign_in):
    order(shop, "made_up_ship", "Red bundle")
    give(shop, buyer="made_up_ship")
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        o = db.session.scalar(select(Order).where(Order.buyer_username == "made_up_ship"))
        o.status = OrderStatus.shipped
        db.session.commit()
    h = sign_in(shop["org"], "manager")
    found, _ = board(client, shop)
    assert found["made_up_ship"]["tab"] == "shipped"
    # The product can still be named afterwards; it stays done.
    name_giveaway(client, shop, h, "made_up_ship")
    assert tabs(client, shop) == {"made_up_ship": "shipped"}


def test_a_manager_marks_a_box_ready_and_anything_new_brings_it_back(app, client, shop, sign_in):
    order(shop, "made_up_picks", CHOICE)
    read(app, shop)
    apply(app, shop)
    url = f"/api/orders/shows/{shop['show']}/ready"
    packer = sign_in(shop["org"], "fulfillment")
    assert client.post(url, json={"buyer": "made_up_picks"}, headers=packer).status_code == 403
    h = sign_in(shop["org"], "manager")
    assert board(client, shop)[0]["made_up_picks"]["mayMarkReady"]
    r = client.post(url, json={"buyer": "made_up_picks"}, headers=h)
    assert r.status_code == 200 and r.get_json() == {"changed": True}
    assert client.post(url, json={"buyer": "made_up_picks"}, headers=h).get_json() == {
        "changed": False
    }  # a second press changes nothing
    found, _ = board(client, shop)
    assert found["made_up_picks"]["tab"] == "pack" and found["made_up_picks"]["readyMarked"]
    with app.app_context():
        # It changed no order and no sale: the app's own gates still stand.
        o = db.session.scalar(select(Order).where(Order.buyer_username == "made_up_picks"))
        assert o.status is OrderStatus.open
        assert [a.action for a in db.session.scalars(select(AuditLog))][-1] == "orders.marked_ready"
        # A question that opens later (the same win read twice, say) puts the box back.
        sale = db.session.scalar(select(Sale).where(Sale.buyer_username == "made_up_picks"))
        db.session.add(
            ReviewItem(
                organization_id=shop["org"],
                show_id=shop["show"],
                sale_id=sale.id,
                reason="possible_duplicate",
                summary="Same win twice?",
            )
        )
        db.session.commit()
    assert tabs(client, shop) == {"made_up_picks": "review"}
    # Marked again it goes back; taken back it returns to Need review.
    client.post(url, json={"buyer": "made_up_picks"}, headers=h)
    assert tabs(client, shop) == {"made_up_picks": "pack"}
    r = client.post(url, json={"buyer": "made_up_picks", "ready": False}, headers=h)
    assert r.get_json() == {"changed": True}
    assert tabs(client, shop) == {"made_up_picks": "review"}


def test_the_show_list_counts_each_tab(app, client, shop, sign_in):
    order(shop, "made_up_paid", "Red bundle")
    order(shop, "made_up_picks", CHOICE)
    read(app, shop)
    apply(app, shop)
    sign_in(shop["org"], "fulfillment")  # every role that sees Orders sees the list
    shows = client.get("/api/orders/board").get_json()["shows"]
    (row,) = [s for s in shows if s["showId"] == shop["show"]]
    assert (row["review"], row["pack"], row["shipped"]) == (1, 1, 0)


def test_badges_need_both_spend_and_shows():
    assert buyers.tier_of(60_000, 8) == "gold"
    assert buyers.tier_of(60_000, 3) == "copper"  # spends a lot, rarely: only copper
    assert buyers.tier_of(1_000, 20) is None  # often, but little
    assert buyers.tier_of(20_000, 4) == "silver"


def test_a_buyer_badge_counts_every_show_they_bought_in(app, client, shop, sign_in):
    order(shop, "made_up_paid", "Red bundle")
    read(app, shop)
    apply(app, shop)
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        for i in range(1, 3):  # two earlier shows, $40 each
            earlier = Show(
                organization_id=shop["org"],
                show_key=f"earlier-{i}",
                started_at=show.started_at - timedelta(days=7 * i),
                show_date=show.show_date - timedelta(days=7 * i),
            )
            db.session.add(earlier)
            db.session.flush()
            db.session.add(
                Sale(
                    organization_id=shop["org"],
                    show_id=earlier.id,
                    buyer_username="made_up_paid",
                    item_text="Red bundle",
                    quantity=1,
                    price_cents=4000,
                    status="paid",
                )
            )
        db.session.commit()
        assert order_board.key("@Made_Up_Paid") == "made_up_paid"
    sign_in(shop["org"], "manager")
    b = board(client, shop)[0]["made_up_paid"]
    assert b["shows"] == 3 and b["tier"] == "copper"  # $25 + $80 paid, three shows


# ------- Found by the money-safety review (2026-11-24): red before the fix, green after -------


def _question(app, shop, buyer, reason):
    with app.app_context():
        sale = db.session.scalar(select(Sale).where(Sale.buyer_username == buyer))
        db.session.add(
            ReviewItem(
                organization_id=shop["org"],
                show_id=shop["show"],
                sale_id=sale.id,
                reason=reason,
                summary="made-up question",
            )
        )
        db.session.commit()


def test_whatnot_decides_a_card_in_doubt_or_two_winners_and_no_mark_packs_past_it(
    app, client, shop, sign_in
):
    order(shop, "made_up_doubt", "Red bundle")
    order(shop, "made_up_twice", "Red bundle")
    read(app, shop)
    apply(app, shop)
    _question(app, shop, "made_up_doubt", "decline_seen")
    _question(app, shop, "made_up_twice", "lot_won_twice")
    h = sign_in(shop["org"], "manager")
    url = f"/api/orders/shows/{shop['show']}/ready"
    found, _ = board(client, shop)
    for name in ("made_up_doubt", "made_up_twice"):
        assert found[name]["tab"] == "review" and not found[name]["mayMarkReady"]
        assert client.post(url, json={"buyer": name}, headers=h).status_code == 409
    assert tabs(client, shop) == {"made_up_doubt": "review", "made_up_twice": "review"}
    r = client.get(f"/api/orders/shows/{shop['show']}/order-slips?only=pack").get_json()
    assert r["slips"] == []


def test_a_possible_duplicate_or_unlisted_sale_holds_the_box_and_stays_off_the_printout(
    app, client, shop, sign_in
):
    order(shop, "made_up_dupe", "Red bundle")
    read(app, shop)
    apply(app, shop)
    _question(app, shop, "made_up_dupe", "possible_duplicate")
    sign_in(shop["org"], "manager")
    assert tabs(client, shop) == {"made_up_dupe": "review"}
    r = client.get(f"/api/orders/shows/{shop['show']}/order-slips?only=pack").get_json()
    assert r["slips"] == []


def test_a_giveaway_read_after_the_mark_brings_the_box_back_whenever_it_was_won(
    app, client, shop, sign_in
):
    order(shop, "made_up_picks", CHOICE)
    read(app, shop)
    apply(app, shop)
    h = sign_in(shop["org"], "manager")
    url = f"/api/orders/shows/{shop['show']}/ready"
    assert client.post(url, json={"buyer": "made_up_picks"}, headers=h).status_code == 200
    assert tabs(client, shop) == {"made_up_picks": "pack"}
    # Won an hour ago on Whatnot, read only now: the manager never saw it.
    give(shop, buyer="made_up_picks", minutes=60)
    read(app, shop)
    assert tabs(client, shop) == {"made_up_picks": "review"}


def test_a_giveaway_whatnot_cancels_after_the_mark_brings_the_box_back(app, client, shop, sign_in):
    order(shop, "made_up_picks", CHOICE)
    g = give(shop, buyer="made_up_picks")
    read(app, shop)
    apply(app, shop)
    h = sign_in(shop["org"], "manager")
    name_giveaway(client, shop, h, "made_up_picks")
    url = f"/api/orders/shows/{shop['show']}/ready"
    assert client.post(url, json={"buyer": "made_up_picks"}, headers=h).status_code == 200
    assert tabs(client, shop) == {"made_up_picks": "pack"}
    shop["fake"].set_order_status(g["id"], "cancelled")
    read(app, shop)
    # Its product is still out of stock: a person must take it off.
    assert tabs(client, shop) == {"made_up_picks": "review"}


def test_hiding_cancelled_orders_never_hides_a_giveaway_that_still_needs_a_person(
    app, client, shop, sign_in
):
    o = order(shop, "made_up_cancel", "Red bundle")
    give(shop, buyer="made_up_cancel")
    read(app, shop)
    apply(app, shop)
    shop["fake"].set_order_status(o["id"], "cancelled")
    read(app, shop)
    apply(app, shop)
    sign_in(shop["org"], "manager")
    found, counts = board(client, shop)
    b = found["made_up_cancel"]
    assert b["tab"] == "review" and b["cancelled"] and not b["hideable"]
    assert counts["cancelled"] == 0

"""The show's Order Slips: the per-show packing document staff built by hand from Whatnot's
shipments tab ("oct 31 -1.pdf": a cover with totals, then one slip per buyer)."""

import pytest
from sqlalchemy import select

from app.db import db
from app.models import AuditLog, Sale, Show, User
from app.services import order_slips, rules
from tests.test_pipeline import run


@pytest.mark.parametrize(
    "item,expected,title_only",
    [
        ("Quick Hit 188", "Quick Hit 188", True),
        ("quick hit #2", "Quick Hit 2", True),
        ("Ultra 19", "Ultra 19", True),
        ("Bundle 2", "Bundle 2", True),
        ("Red bundle #752", "Red 752", True),
        ("Flash Yomi Drone", "Flash", False),
        ("Fitness Ball", "Listing", False),
        ("CHOOSE ANY PRODUCT YOU WANT #73", "Listing 73", True),  # the winner picks in chat
        ("", "Listing", False),
    ],
)
def test_lot_label_reads_the_auction_number_from_the_listing_title(item, expected, title_only):
    assert rules.lot_label(item, rules.auction_type(item)) == expected
    assert rules.is_lot_title(item) is title_only


def win(buyer, title, price):
    """A win as the extension writes it from Whatnot's video player."""
    return ("", f"{buyer} won the auction for {title} for ${price}", "player")


def build(app, show_id):
    with app.app_context():
        return order_slips.build(db.session.get(Show, show_id))


def test_order_slips_follow_the_shop_sheet(app, world):
    world["say"](
        app,
        [
            win("jess88", "Quick Hit 2", "14.89"),
            win("lani808", "Red bundle", "25.00"),
            win("jess88", "Ultra 20", "37.65"),
            ("", "jess88 won the giveaway!"),
            win("kat.c", "Red bundle", "25.00"),
            ("host_shop", "cancelled for kat.c"),
            win("Amy", "Quick Hit 3", "8.00"),
        ],
    )
    run(app, world["show"])
    with app.app_context():  # a two-pick lot: two units, one price
        s = db.session.scalar(select(Sale).where(Sale.buyer_username == "lani808"))
        s.quantity = 2
        db.session.commit()
    doc = build(app, world["show"])

    # Cancelled lots do not ship: kat.c has no slip. A giveaway is an item (it is in the box).
    assert doc["totals"] == {"buyers": 3, "items": 6, "gifts": 0}
    assert [b["username"] for b in doc["summary"]] == ["Amy", "jess88", "lani808"]  # A-Z
    assert [(b["username"], b["items"]) for b in doc["slips"]] == [
        ("jess88", 3),
        ("lani808", 2),
        ("Amy", 1),
    ]  # most items first
    jess = doc["slips"][0]
    assert [(r["auction"], r["product"], r["priceCents"]) for r in jess["rows"]] == [
        ("Quick Hit 2", "No Pick", 1489),  # the title only names the lot: nobody picked yet
        ("Ultra 20", "No Pick", 3765),
        ("Gift", "Gift", None),
    ]
    assert jess["needsReview"] is True and jess["orderId"] is not None
    # Each unit is a row to tick; the lot's price is on the first, "-" (continued) after.
    lani = doc["slips"][1]
    assert [
        (r["auction"], r["product"], r["priceCents"], r["continued"]) for r in lani["rows"]
    ] == [("Red", "Red bundle", 5000, False), ("Red", "Red bundle", None, True)]
    assert lani["needsReview"] is False


def test_gift_counts_add_up_per_buyer_and_show(app, world):
    world["say"](
        app,
        [win("jess88", "Quick Hit 127", "36.44"), win("jess88", "Quick Hit 128", "50.23")],
    )
    run(app, world["show"])
    with app.app_context():
        first, second = db.session.scalars(select(Sale).order_by(Sale.id)).all()
        order_slips.set_gift_count(first, 2)
        order_slips.set_gift_count(second, 1)
        db.session.commit()
        for bad in (-1, order_slips.MAX_GIFTS + 1, "2", True, None, 1.5):
            with pytest.raises(ValueError):
                order_slips.set_gift_count(first, bad)
    doc = build(app, world["show"])
    assert doc["totals"]["gifts"] == 3
    assert doc["summary"] == [{"username": "jess88", "items": 2, "gifts": 3}]
    assert [r["giftCount"] for r in doc["slips"][0]["rows"]] == [2, 1]


def signed_in(app, client, org_id):
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "acceptLicence": True,
            "organization": "O",
            "name": "O",
            "email": "o@x.test",
            "password": "correct-horse-battery",
        },
        headers=h,
    )
    with app.app_context():
        u = db.session.scalar(select(User))
        u.organization_id = org_id
        db.session.commit()
    return h


def test_order_slip_routes_permissions_and_audit(client, app, world):
    world["say"](app, [win("jess88", "Quick Hit 2", "14.89")])
    run(app, world["show"])
    h = signed_in(app, client, world["org"])
    with app.app_context():
        sale_id = db.session.scalar(select(Sale.id))

    r = client.get(f"/api/orders/shows/{world['show']}/order-slips")
    assert r.status_code == 200 and r.get_json()["totals"]["items"] == 1
    assert client.get("/api/orders/shows/999999/order-slips").status_code == 404

    r = client.patch(f"/api/orders/sales/{sale_id}/gifts", json={"giftCount": 2}, headers=h)
    assert r.status_code == 200 and r.get_json() == {"saleId": sale_id, "giftCount": 2}
    assert (
        client.get(f"/api/orders/shows/{world['show']}/order-slips").get_json()["totals"]["gifts"]
        == 2
    )
    assert (
        client.patch(f"/api/orders/sales/{sale_id}/gifts", json={"giftCount": -1}, headers=h)
    ).status_code == 400
    assert (
        client.patch("/api/orders/sales/999999/gifts", json={"giftCount": 1}, headers=h)
    ).status_code == 404
    with app.app_context():
        log = db.session.scalar(select(AuditLog).where(AuditLog.action == "sale.gifts"))
        assert log.details == {"before": 0, "after": 2}

    # A viewer may read the slips but not change what goes in the box.
    token = (
        client.post("/api/team/invites", json={"email": "v@x.test", "role": "viewer"}, headers=h)
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    viewer = app.test_client()
    hv = {"X-CSRF-Token": viewer.get("/api/auth/csrf").get_json()["token"]}
    viewer.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "V", "password": "correct-horse-battery"},
        headers=hv,
    )
    assert viewer.get(f"/api/orders/shows/{world['show']}/order-slips").status_code == 200
    assert (
        viewer.patch(f"/api/orders/sales/{sale_id}/gifts", json={"giftCount": 0}, headers=hv)
    ).status_code == 403


def test_an_app_sale_with_a_huge_quantity_prints_a_bounded_slip(app, world):
    """A chat claim of 999 units prints at most MAX_UNIT_ROWS lines and asks a person, as a
    Whatnot lot does (architecture review 2026-11-07, #16)."""
    world["say"](app, [("", "big_buyer won Red bundle x999")])
    run(app, world["show"])
    with app.app_context():
        assert db.session.scalar(select(Sale.quantity)) == 999
        doc = order_slips.build(db.session.get(Show, world["show"]))
    [slip] = [b for b in doc["slips"] if b["username"] == "big_buyer"]
    assert len(slip["rows"]) == order_slips.MAX_UNIT_ROWS and slip["needsReview"]


def test_only_ready_keeps_the_buyers_whose_order_is_ready_to_pick(client, app, world):
    """The Orders page's "Print ready slips": a slip for every order that is Ready to pick
    (every item paid and named, picking not started); the others are counted, not printed."""
    world["say"](
        app,
        [
            win("jess88", "Quick Hit 2", "14.89"),  # the title names no product: Needs review
            win("lani808", "Red bundle", "25.00"),  # matched and charged at the win: ready
            ("", "kat.c won the giveaway!"),  # a gift alone is no order
        ],
    )
    run(app, world["show"])
    with app.app_context():
        show = db.session.get(Show, world["show"])
        everyone = order_slips.build(show)
        ready = order_slips.build(show, only="ready")
        with pytest.raises(ValueError):
            order_slips.build(show, only="shipped")
    assert [(b["username"], b["orderStatus"]) for b in everyone["slips"]] == [
        ("jess88", "open"),
        ("kat.c", None),
        ("lani808", "paid"),
    ]  # one item each, so A-Z
    assert everyone["only"] is None and everyone["leftOut"] == 0
    assert [b["username"] for b in ready["slips"]] == ["lani808"]
    assert ready["only"] == "ready" and ready["leftOut"] == 2
    assert ready["totals"] == {"buyers": 1, "items": 1, "gifts": 0}
    assert ready["summary"] == [{"username": "lani808", "items": 1, "gifts": 0}]

    signed_in(app, client, world["org"])
    url = f"/api/orders/shows/{world['show']}/order-slips"
    r = client.get(url + "?only=ready")
    assert r.status_code == 200 and r.get_json()["totals"]["buyers"] == 1
    assert client.get(url + "?only=shipped").status_code == 400
    assert client.get(url + "?only=").get_json()["only"] is None


def test_only_ready_holds_back_a_slip_that_still_needs_a_person(app, world):
    """Ready means the products are confirmed. With Whatnot's lots read, a slip is held back
    when one of its lots has no product (a win the app never recorded), or when it has a sale
    Whatnot does not list. Buyers are made up."""
    from app.models import WhatnotLot, WhatnotShipment

    world["say"](
        app,
        [
            win("buyer_a", "Red bundle #1", "25.00"),
            win("buyer_b", "Red bundle #2", "25.00"),
            win("buyer_c", "Red bundle #3", "25.00"),
        ],
    )
    run(app, world["show"])
    with app.app_context():

        def parcel(buyer, key):
            row = WhatnotShipment(
                organization_id=world["org"],
                row_key=key * 64,
                recipient=buyer,
                show_id=world["show"],
                status="Ready to Ship",
            )
            db.session.add(row)
            db.session.flush()
            return row.id

        def lot(parcel_id, buyer, order_id, title, number, cents):
            db.session.add(
                WhatnotLot(
                    organization_id=world["org"],
                    show_id=world["show"],
                    shipment_id=parcel_id,
                    whatnot_order_id=order_id,
                    recipient=buyer,
                    title=title,
                    lot_number=number,
                    price_cents=cents,
                )
            )

        a, b = parcel("buyer_a", "a"), parcel("buyer_b", "b")
        lot(a, "buyer_a", "1001", "Red bundle #1", 1, 2500)
        lot(b, "buyer_b", "1002", "Red bundle #2", 2, 2500)
        # A win of buyer_b's that the app never recorded: nobody has said which product.
        lot(b, "buyer_b", "1003", "Quick Hit 9", 9, 700)
        # buyer_c's sale is in the app, but Whatnot lists no lot for it.
        db.session.commit()

        show = db.session.get(Show, world["show"])
        everyone = order_slips.build(show)
        ready = order_slips.build(show, only="ready")
    by_buyer = {b["username"]: b for b in everyone["slips"]}
    # All three orders are paid: the order's state alone would call all three ready.
    assert {u: b["orderStatus"] for u, b in by_buyer.items()} == {
        "buyer_a": "paid",
        "buyer_b": "paid",
        "buyer_c": "paid",
    }
    assert [r["product"] for r in by_buyer["buyer_b"]["rows"]] == ["Red bundle", "No Pick"]
    assert [r["onWhatnot"] for r in by_buyer["buyer_c"]["rows"]] == [False]
    assert [b["username"] for b in ready["slips"]] == ["buyer_a"]
    assert ready["leftOut"] == 2
    # The count the "Ready to pick" switch shows is the same whichever document is open.
    assert (everyone["ready"], ready["ready"]) == (1, 1)
    assert ready["totals"] == {"buyers": 1, "items": 1, "gifts": 0}

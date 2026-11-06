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

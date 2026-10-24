import pytest
from sqlalchemy import select

from app.db import db
from app.models import Order, OrderStatus, ReviewItem, Sale, SaleStatus, Show, User
from app.services import orders, pipeline, reconcile
from app.services.inventory import Actor
from app.services.orders import OrderError
from tests.test_pipeline import level, open_reviews, run, sales


def order_of(app, buyer):
    with app.app_context():
        o = db.session.scalar(select(Order).where(Order.buyer_username == buyer))
        return (o.status.value, len(o.sales), orders.verified(o)) if o else None


def owner(app, org_id):
    u = User(
        organization_id=org_id,
        email="o@x.test",
        name="O",
        password_hash="x",
        role="owner",
        session_token="t",
    )
    db.session.add(u)
    db.session.flush()
    return u


CSV = """Order ID,Buyer,Product,Quantity,Sold Price,Status
W1,lani808,Red bundle,1,25.00,Paid
W2,lani808,Red bundle,2,50.00,Paid
W3,kat.c,Red bundle,1,25.00,Refunded
W4,stranger,Red bundle,1,25.00,Paid
"""


def test_sales_group_into_one_order_per_buyer_and_show(app, world):
    world["say"](
        app,
        [
            ("", "lani808 won Red bundle"),
            ("", "lani808 won Red bundle x2"),
            ("", "kat.c won Red bundle"),
        ],
    )
    run(app, world["show"])
    assert order_of(app, "lani808") == ("open", 2, False)
    assert order_of(app, "kat.c") == ("open", 1, False)
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(Order)) == 2


def test_order_follows_its_sales_until_fulfilment(app, world):
    world["say"](app, [("", "lani808 won Red bundle"), ("host_shop", "cancelled for lani808")])
    run(app, world["show"])
    assert order_of(app, "lani808") == ("cancelled", 1, False)


def test_unpaid_order_cannot_be_picked(app, world):
    world["say"](app, [("", "lani808 won Red bundle")])
    run(app, world["show"])
    with app.app_context():
        o = db.session.scalar(select(Order))
        with pytest.raises(OrderError, match="not paid"):
            orders.advance(o, OrderStatus.picking, actor_label="o")


def test_reconcile_preview_then_apply_commits_and_releases_stock(app, world):
    world["say"](
        app,
        [
            ("", "lani808 won Red bundle"),
            ("", "lani808 won Red bundle x2"),
            ("", "kat.c won Red bundle"),
            ("", "dave-77 won Red bundle"),
        ],
    )
    run(app, world["show"])
    # 1 + 2 + 1 + 1 reserved against 3 on the shelf: oversold by 2, nothing refused
    assert level(app, world["red"]) == (3, 5)
    with app.app_context():
        show = db.session.get(Show, world["show"])
        p = reconcile.plan(show, reconcile.parse_csv(CSV))
        assert [r.action for r in p.rows] == ["mark_paid", "mark_paid", "cancel", "unmatched"]
        assert p.summary == {"mark_paid": 2, "cancel": 1, "unmatched": 1, "uncovered": 1}
        dave = db.session.scalar(select(Sale).where(Sale.buyer_username == "dave-77"))
        assert p.uncovered == [dave.id]
        db.session.rollback()
    assert level(app, world["red"]) == (3, 5)  # preview changed nothing
    with app.app_context():
        show = db.session.get(Show, world["show"])
        u = owner(app, world["org"])
        p = reconcile.plan(show, reconcile.parse_csv(CSV))
        reconcile.apply(show, p, actor=Actor.user(u), user=u, import_id="imp1")
        db.session.commit()
    assert sorted(sales(app, world["show"])) == sorted(
        [
            ("lani808", "paid", "RED", 1),
            ("lani808", "paid", "RED", 2),
            ("kat.c", "cancelled", "RED", 1),
            ("dave-77", "reserved", "RED", 1),
        ]
    )
    # 3 committed for lani808, kat.c's refund released hers, dave's 1 still held: 1 to buy
    assert level(app, world["red"]) == (0, 1)
    assert order_of(app, "lani808") == ("paid", 2, True)
    assert order_of(app, "kat.c") == ("cancelled", 1, False)
    # dave: not in the file; stranger: in the file but no sale.
    assert sorted(r for r, _ in open_reviews(app)) == [
        "no_payment_record",
        "unmatched_payment",
    ]
    with app.app_context():
        s = db.session.scalar(select(Sale).where(Sale.buyer_username == "lani808"))
        assert s.external_order_id == "W1" and s.paid_at is not None
        # re-applying the same file is a no-op
        show = db.session.get(Show, world["show"])
        p = reconcile.plan(show, reconcile.parse_csv(CSV))
        assert [r.action for r in p.rows] == ["unchanged", "unchanged", "unchanged", "unmatched"]
        n = db.session.scalar(select(db.func.count()).select_from(ReviewItem))
        reconcile.apply(
            show,
            p,
            actor=Actor.user(
                owner(app, world["org"]) if False else db.session.scalar(select(User))
            ),
            import_id="imp2",
        )
        db.session.commit()
        assert db.session.scalar(select(db.func.count()).select_from(ReviewItem)) == n
    assert level(app, world["red"]) == (0, 1)


def test_parse_csv_is_tolerant_about_headers_and_statuses():
    rows = reconcile.parse_csv(
        "username,listing title,status\n@lani808,Red bundle,completed\nkat.c,Red bundle,\n"
        "bob,Red bundle,weird\n,x,paid\n"
    )
    assert [(r.buyer, r.status, r.action) for r in rows] == [
        ("lani808", "paid", ""),
        ("kat.c", "paid", ""),
        ("bob", "unknown", "skip"),
        ("", "paid", "skip"),
    ]
    with pytest.raises(reconcile.ReconcileError, match="buyer column"):
        reconcile.parse_csv("a,b\n1,2\n")


def test_fulfilment_flow_pick_pack_ship_and_pick_list(app, world):
    world["say"](app, [("", "lani808 won Red bundle x2"), ("", "kat.c won Red bundle")])
    run(app, world["show"])
    with app.app_context():
        show = db.session.get(Show, world["show"])
        u = owner(app, world["org"])
        reconcile.apply(
            show,
            reconcile.plan(
                show,
                reconcile.parse_csv(
                    "Buyer,Product,Status\nlani808,Red bundle,paid\nkat.c,Red bundle,paid\n"
                ),
            ),
            actor=Actor.user(u),
            import_id="i",
        )
        db.session.commit()
        assert orders.pick_list(show) == [
            {
                "skuId": world["red"],
                "code": "RED",
                "product": "Red bundle",
                "variant": "",
                "quantity": 3,
                "orders": 2,
            }
        ]
        o = db.session.scalar(select(Order).where(Order.buyer_username == "lani808"))
        orders.advance(o, OrderStatus.picking, actor_label="o")
        with pytest.raises(OrderError):
            orders.advance(o, OrderStatus.shipped, actor_label="o")  # must pack first
        orders.advance(o, OrderStatus.packed, actor_label="o")
        orders.advance(o, OrderStatus.shipped, actor_label="o", tracking="1Z999")
        db.session.commit()
        assert o.tracking == "1Z999" and o.shipped_at is not None
        assert {s.status for s in o.sales} == {SaleStatus.fulfilled}
        assert orders.pick_list(show) == [
            {
                "skuId": world["red"],
                "code": "RED",
                "product": "Red bundle",
                "variant": "",
                "quantity": 1,
                "orders": 1,
            }
        ]
        # a cancel line after shipping flags, releases nothing
        pipeline.set_sale_status(o.sales[0], SaleStatus.cancelled, reason="late", actor=Actor())
        assert o.sales[0].flagged is True
        db.session.rollback()
    assert level(app, world["red"]) == (0, 0)


def test_order_routes_and_permissions(client, app, world):
    world["say"](app, [("", "lani808 won Red bundle")])
    run(app, world["show"])
    r = client.get("/api/auth/csrf")
    h = {"X-CSRF-Token": r.get_json()["token"]}
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
        u.organization_id = world["org"]
        db.session.commit()
    assert client.get("/api/orders?status=queue").get_json()["orders"] == []
    opened = client.get("/api/orders?status=open").get_json()["orders"]
    assert (
        opened[0]["buyer"] == "lani808"
        and opened[0]["verified"] is False
        and opened[0]["totalCents"] == 2500
    )
    oid = opened[0]["id"]
    assert (
        client.post(f"/api/orders/{oid}/status", json={"status": "picking"}, headers=h).status_code
        == 409
    )
    r = client.post(
        f"/api/orders/shows/{world['show']}/reconcile",
        json={"csv": "Buyer,Product,Status\nlani808,Red bundle,paid\n", "apply": True},
        headers=h,
    )
    assert r.status_code == 200 and r.get_json()["summary"] == {"mark_paid": 1}
    assert client.get("/api/orders?status=queue").get_json()["orders"][0]["status"] == "paid"
    assert (
        client.get(f"/api/orders/shows/{world['show']}/pick-list").get_json()["items"][0][
            "quantity"
        ]
        == 1
    )
    r = client.post(f"/api/orders/{oid}/status", json={"status": "picking"}, headers=h)
    assert r.get_json()["order"]["status"] == "picking"
    # the slip's "Packed by": initials typed in advance stay once the order is packed
    r = client.patch(f"/api/orders/{oid}", json={"packedBy": " JS "}, headers=h)
    assert r.get_json()["order"]["packedBy"] == "JS"
    assert client.get(f"/api/orders/{oid}").get_json()["order"]["gifts"] == []
    assert client.get("/api/orders/buyers/LANI808").get_json()["orders"][0]["id"] == oid
    # fulfillment can pick but not reconcile
    token = (
        client.post(
            "/api/team/invites", json={"email": "p@x.test", "role": "fulfillment"}, headers=h
        )
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    pat = app.test_client()
    hp = {"X-CSRF-Token": pat.get("/api/auth/csrf").get_json()["token"]}
    pat.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "P", "password": "correct-horse-battery"},
        headers=hp,
    )
    assert (
        pat.post(f"/api/orders/{oid}/status", json={"status": "packed"}, headers=hp).status_code
        == 200
    )
    assert client.get(f"/api/orders/{oid}").get_json()["order"]["packedBy"] == "JS"
    assert (
        pat.post(
            f"/api/orders/shows/{world['show']}/reconcile", json={"csv": "x"}, headers=hp
        ).status_code
        == 403
    )


def test_packing_slip_gets_packer_name_and_the_buyer_gift_lines(app, world):
    world["say"](
        app,
        [
            ("", "lani808 won Red bundle"),
            ("", "lani808 won the giveaway!"),
            ("host_shop", "gift to lani808 - sticker pack"),
            ("viewer", "gift to lani808 - a car"),  # a viewer's line never reaches the slip
            ("host_shop", "gift to kat.c - sticker pack"),
        ],
    )
    run(app, world["show"])
    with app.app_context():
        show = db.session.get(Show, world["show"])
        u = owner(app, world["org"])
        reconcile.apply(
            show,
            reconcile.plan(
                show, reconcile.parse_csv("Buyer,Product,Status\nlani808,Red bundle,paid\n")
            ),
            actor=Actor.user(u),
            import_id="i",
        )
        o = db.session.scalar(select(Order).where(Order.buyer_username == "lani808"))
        assert orders.gifts_for(o) == [
            {"item": "", "seq": 2},
            {"item": "sticker pack", "seq": 3},
        ]
        orders.advance(o, OrderStatus.picking, actor_label="o")
        orders.advance(o, OrderStatus.packed, actor_label="o", packed_by="Olive Owner")
        assert o.packed_by == "Olive Owner" and o.packed_at is not None
        # the first packer's name stays even if the order is packed again
        orders.advance(o, OrderStatus.picking, actor_label="o")
        orders.advance(o, OrderStatus.packed, actor_label="o", packed_by="Someone Else")
        assert o.packed_by == "Olive Owner"
        db.session.rollback()

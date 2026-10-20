"""Receiving deliveries, shelf counts and the readable ledger (services/stock.py)."""

from sqlalchemy import select

from app.db import db
from app.models import AuditLog, InventoryMovement, Sku
from app.services import pipeline, stock

PW = "correct-horse-battery"


def owner_client(client):
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={"organization": "Shop", "name": "O", "email": "o@shop.test", "password": PW},
        headers=h,
    )
    return h


def make_red(client, h, on_hand=0):
    r = client.post(
        "/api/products",
        json={"name": "Red bundle", "skus": [{"code": "RED", "priceCents": 2500}]},
        headers=h,
    )
    sku_id = r.get_json()["product"]["skus"][0]["id"]
    if on_hand:
        client.post(f"/api/inventory/{sku_id}/adjust", json={"delta": on_hand}, headers=h)
    return sku_id


def levels(client):
    return {s["code"]: s for s in client.get("/api/inventory").get_json()["skus"]}


# ---------- receive ----------


def test_receive_adds_known_and_new_items_in_one_delivery(client, app):
    h = owner_client(client)
    red = make_red(client, h, on_hand=2)
    r = client.post(
        "/api/inventory/receive",
        json={
            "receiptId": "delivery-001",
            "note": "Invoice 4411",
            "lines": [
                {"skuId": red, "quantity": 5},
                {
                    "new": {
                        "name": "Holo slab",
                        "code": "holo",
                        "priceCents": 1500,
                        "lowStockAt": 2,
                    },
                    "quantity": "8",
                },
            ],
        },
        headers=h,
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["units"] == 13 and body["alreadyApplied"] is False
    assert [(x["code"], x["quantity"], x["onHand"], x["created"]) for x in body["lines"]] == [
        ("RED", 5, 7, False),
        ("HOLO", 8, 8, True),
    ]
    lv = levels(client)
    assert lv["RED"]["onHand"] == 7 and lv["RED"]["lastReceivedAt"]
    assert lv["HOLO"]["onHand"] == 8 and lv["HOLO"]["lowStockAt"] == 2
    assert lv["HOLO"]["priceCents"] == 1500 and lv["HOLO"]["productName"] == "Holo slab"
    with app.app_context():
        moves = db.session.scalars(
            select(InventoryMovement).where(InventoryMovement.source_type == "receive")
        ).all()
        assert sorted(m.source_id for m in moves) == ["delivery-001:1", "delivery-001:2"]
        assert {m.reason for m in moves} == {"Invoice 4411"}
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "inventory.received"))

    # The same form submitted twice (double click, retry) adds nothing.
    again = client.post(
        "/api/inventory/receive",
        json={"receiptId": "delivery-001", "lines": [{"skuId": red, "quantity": 5}]},
        headers=h,
    )
    assert again.status_code == 200 and again.get_json()["alreadyApplied"] is True
    assert levels(client)["RED"]["onHand"] == 7


def test_receive_is_all_or_nothing_and_validates_lines(client):
    h = owner_client(client)
    red = make_red(client, h)

    def post(lines, **extra):
        return client.post("/api/inventory/receive", json={"lines": lines, **extra}, headers=h)

    assert post([]).status_code == 400
    assert "quantity" in post([{"skuId": red, "quantity": 0}]).get_json()["error"]
    assert "whole number" in post([{"skuId": red, "quantity": "two"}]).get_json()["error"]
    assert (
        "twice"
        in post([{"skuId": red, "quantity": 1}, {"skuId": red, "quantity": 2}]).get_json()["error"]
    )
    assert "name" in post([{"new": {"name": "  "}, "quantity": 1}]).get_json()["error"]
    assert post([{"skuId": 999999, "quantity": 1}]).status_code == 404
    assert post([{"skuId": red, "quantity": 1}], receiptId="no spaces!").status_code == 400
    # A new item that clashes with an existing product fails the whole delivery.
    r = post([{"skuId": red, "quantity": 4}, {"new": {"name": "Red bundle"}, "quantity": 1}])
    assert r.status_code == 409
    assert levels(client)["RED"]["onHand"] == 0


# ---------- count ----------


def test_count_sets_the_shelf_and_reports_differences(client, app):
    h = owner_client(client)
    red = make_red(client, h, on_hand=10)
    ultra = client.post("/api/products", json={"name": "Ultra pack"}, headers=h).get_json()[
        "product"
    ]["skus"][0]["id"]
    client.post(f"/api/inventory/{ultra}/adjust", json={"delta": 4}, headers=h)
    r = client.post(
        "/api/inventory/count",
        json={
            "countId": "count-oct20",
            "note": "Tuesday shelf count",
            "lines": [{"skuId": red, "counted": 7}, {"skuId": ultra, "counted": 4}],
        },
        headers=h,
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert (body["changed"], body["unchanged"], body["net"]) == (1, 1, -3)
    assert [(x["code"], x["before"], x["after"], x["delta"]) for x in body["lines"]] == [
        ("RED", 10, 7, -3),
        ("ULTRA-PACK", 4, 4, 0),
    ]
    lv = levels(client)
    assert lv["RED"]["onHand"] == 7 and lv["RED"]["lastCountedAt"]
    assert lv["ULTRA-PACK"]["lastCountedAt"] is None  # unchanged rows write nothing
    # Replaying the same count moves nothing.
    again = client.post(
        "/api/inventory/count",
        json={"countId": "count-oct20", "lines": [{"skuId": red, "counted": 7}]},
        headers=h,
    )
    assert again.get_json()["changed"] == 0
    with app.app_context():
        assert (
            db.session.scalar(
                select(db.func.count())
                .select_from(InventoryMovement)
                .where(InventoryMovement.source_type == "count")
            )
            == 1
        )
    bad = client.post(
        "/api/inventory/count", json={"lines": [{"skuId": red, "counted": -1}]}, headers=h
    )
    assert bad.status_code == 400


def test_count_below_what_sales_hold_makes_the_item_short(app, world):
    world["say"](app, [("", "lani808 won Red bundle x2")])
    with app.app_context():
        pipeline.process_show(world["show"])
        db.session.commit()
        stock.count(
            world["org"],
            [{"skuId": world["red"], "counted": 1}],
            note=None,
            count_id="count-1",
            actor=stock.Actor(),
        )
        db.session.commit()
        s = db.session.get(Sku, world["red"])
        assert (s.on_hand, s.reserved, s.short) == (1, 2, 1)


# ---------- reading ----------


def test_activity_names_the_buyer_and_show_for_sale_movements(app, world):
    world["say"](app, [("", "lani808 won Red bundle x2")])
    with app.app_context():
        pipeline.process_show(world["show"])
        db.session.commit()
        rows = stock.activity(world["org"])
        assert [r["kind"] for r in rows] == ["reserve", "restock"]
        assert rows[0]["sale"]["buyer"] == "lani808"
        assert rows[0]["sale"]["showId"] == world["show"]
        assert rows[0]["skuCode"] == "RED" and rows[0]["productName"] == "Red bundle"
        assert rows[1]["sale"] is None
        lv = {x["code"]: x for x in stock.levels(world["org"])}
        assert lv["RED"]["sold"] == 2 and lv["RED"]["reserved"] == 2


def test_activity_route_and_viewer_cannot_receive(app, client):
    h = owner_client(client)
    red = make_red(client, h, on_hand=3)
    acts = client.get("/api/inventory/activity").get_json()["movements"]
    assert acts[0]["skuCode"] == "RED" and acts[0]["quantity"] == 3
    per = client.get(f"/api/inventory/{red}/movements").get_json()["movements"]
    assert per[0]["productName"] == "Red bundle"

    token = (
        client.post("/api/team/invites", json={"email": "v@shop.test", "role": "viewer"}, headers=h)
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    v = app.test_client()
    hv = {"X-CSRF-Token": v.get("/api/auth/csrf").get_json()["token"]}
    v.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "V", "password": PW},
        headers=hv,
    )
    assert v.get("/api/inventory/activity").status_code == 200
    r = v.post(
        "/api/inventory/receive", json={"lines": [{"skuId": red, "quantity": 1}]}, headers=hv
    )
    assert r.status_code == 403
    r = v.post("/api/inventory/count", json={"lines": [{"skuId": red, "counted": 1}]}, headers=hv)
    assert r.status_code == 403

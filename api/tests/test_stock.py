"""Receiving deliveries, shelf counts and the readable ledger (services/stock.py)."""

from sqlalchemy import select

from app.db import db
from app.models import AuditLog, InventoryMovement, Sku
from app.services import inventory, pipeline, stock

PW = "correct-horse-battery"


def owner_client(client):
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "acceptLicence": True,
            "organization": "Shop",
            "name": "O",
            "email": "o@shop.test",
            "password": PW,
        },
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


def test_count_below_what_is_held_makes_the_item_short(app, world):
    # Sales are paid (committed) at the win, so they hold nothing; a hold comes straight from
    # the ledger here, which still lets a count land below it.
    with app.app_context():
        inventory.reserve(world["red"], 2, source_type="test", source_id="hold")
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
        # paid at the win: the reserve is committed at once
        assert [r["kind"] for r in rows] == ["commit", "reserve", "restock"]
        assert rows[0]["sale"]["buyer"] == "lani808"
        assert rows[0]["sale"]["showId"] == world["show"]
        assert rows[0]["skuCode"] == "RED" and rows[0]["productName"] == "Red bundle"
        assert rows[1]["sale"]["buyer"] == "lani808"
        assert rows[2]["sale"] is None
        lv = {x["code"]: x for x in stock.levels(world["org"])}
        assert lv["RED"]["sold"] == 2 and lv["RED"]["reserved"] == 0


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


# ---------- weight ----------


def test_weight_is_set_on_create_receive_and_edit_and_can_be_cleared(client, app):
    h = owner_client(client)
    r = client.post(
        "/api/products",
        json={"name": "Red bundle", "skus": [{"code": "RED", "priceCents": 2500, "weightOz": 4.5}]},
        headers=h,
    )
    red = r.get_json()["product"]["skus"][0]
    assert red["weightOz"] == 4.5

    # A new item received in a delivery keeps the weight it was added with.
    r = client.post(
        "/api/inventory/receive",
        json={"lines": [{"new": {"name": "Holo slab", "weightOz": "20"}, "quantity": 3}]},
        headers=h,
    )
    assert r.status_code == 201, r.get_json()
    lv = levels(client)
    assert lv["RED"]["weightOz"] == 4.5 and lv["HOLO-SLAB"]["weightOz"] == 20.0

    # Typed in pounds by the page (1.333 lb = 21.328 oz): kept to 0.01 oz.
    r = client.patch(f"/api/skus/{red['id']}", json={"weightOz": 21.328}, headers=h)
    assert r.status_code == 200 and r.get_json()["sku"]["weightOz"] == 21.33
    # Other edits leave the weight alone; null clears it.
    client.patch(f"/api/skus/{red['id']}", json={"lowStockAt": 2}, headers=h)
    assert levels(client)["RED"]["weightOz"] == 21.33
    r = client.patch(f"/api/skus/{red['id']}", json={"weightOz": None}, headers=h)
    assert r.get_json()["sku"]["weightOz"] is None

    said = [e["summary"] for e in client.get("/api/audit").get_json()["entries"]]
    assert "Edited item RED: weight 1 lb 5.33 oz → not weighed" in said
    assert "Edited item RED: weight 4.5 oz → 1 lb 5.33 oz" in said
    with app.app_context():
        entry = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "sku.updated").order_by(AuditLog.id)
        ).first()
        assert entry.details["after"]["weightOz"] == 21.33


def test_a_bad_weight_is_refused_and_moves_nothing(client):
    h = owner_client(client)
    red = make_red(client, h, on_hand=2)
    for bad, words in [
        (0, "more than 0"),
        (-3, "more than 0"),
        (0.004, "more than 0"),
        ("heavy", "number of ounces"),
        ("NaN", "number of ounces"),
        (True, "number of ounces"),
        ([4], "number of ounces"),
        (2400.5, "over 150 lb"),
        ("1e30", "over 150 lb"),
    ]:
        r = client.patch(f"/api/skus/{red}", json={"weightOz": bad}, headers=h)
        assert r.status_code == 400 and words in r.get_json()["error"], (bad, r.get_json())
    assert levels(client)["RED"]["weightOz"] is None
    assert client.patch(f"/api/skus/{red}", json={"weightOz": 2400}, headers=h).status_code == 200

    # A delivery with a badly weighed new item is refused whole.
    r = client.post(
        "/api/inventory/receive",
        json={
            "lines": [
                {"skuId": red, "quantity": 4},
                {"new": {"name": "Holo slab", "weightOz": -1}, "quantity": 1},
            ]
        },
        headers=h,
    )
    assert r.status_code == 400 and "more than 0" in r.get_json()["error"]
    assert levels(client)["RED"]["onHand"] == 2 and "HOLO-SLAB" not in levels(client)
    r = client.post("/api/products", json={"name": "Ultra", "skus": [{"weightOz": "x"}]}, headers=h)
    assert r.status_code == 400


# ---------- renaming ----------


def test_renaming_a_product_keeps_chat_and_the_sheet_matching_the_old_name(client):
    h = owner_client(client)
    make_red(client, h, on_hand=2)
    client.post("/api/products", json={"name": "Holo slab", "skus": [{"code": "HOLO"}]}, headers=h)
    pid = levels(client)["RED"]["productId"]

    r = client.patch(f"/api/products/{pid}", json={"name": "  Red mystery bundle "}, headers=h)
    assert r.status_code == 200 and r.get_json()["product"]["name"] == "Red mystery bundle"
    red = levels(client)["RED"]
    assert red["productName"] == "Red mystery bundle" and red["code"] == "RED"  # code is kept
    assert {a["alias"] for a in red["aliases"]} == {"red bundle", "red mystery bundle"}

    # The stock sheet still says "Red bundle": it updates this item, it does not add a new one.
    r = client.post(
        "/api/inventory/import", json={"csv": "Item,Current Stock\nRed bundle,5\n"}, headers=h
    )
    assert r.get_json()["summary"] == {"set": 1} and r.get_json()["rows"][0]["before"] == 2

    # Another item's name, in any case, is refused; so is a blank one. Nothing changes.
    for bad, status in [("holo SLAB", 409), ("   ", 400), ("​", 400)]:
        r = client.patch(f"/api/products/{pid}", json={"name": bad}, headers=h)
        assert r.status_code == status, (bad, r.get_json())
    assert levels(client)["RED"]["productName"] == "Red mystery bundle"
    # Changing only the case of its own name is fine.
    r = client.patch(f"/api/products/{pid}", json={"name": "Red Mystery Bundle"}, headers=h)
    assert r.status_code == 200

    # A new name that is already another item's chat name does not take it over.
    client.post(
        f"/api/skus/{levels(client)['HOLO']['id']}/aliases", json={"alias": "ultra"}, headers=h
    )
    client.patch(f"/api/products/{pid}", json={"name": "Ultra"}, headers=h)
    lv = levels(client)
    assert "ultra" in {a["alias"] for a in lv["HOLO"]["aliases"]}
    assert "ultra" not in {a["alias"] for a in lv["RED"]["aliases"]}

    said = [e["summary"] for e in client.get("/api/audit").get_json()["entries"]]
    assert "Product: renamed “Red bundle” to “Red mystery bundle”" in said

import threading

import pytest

from app.db import db
from app.models import MovementKind, Organization, Sku
from app.services import catalog, inventory
from app.services.inventory import Actor, InventoryError


@pytest.fixture
def org(app):
    with app.app_context():
        o = Organization(name="Shop", slug="shop")
        db.session.add(o)
        db.session.commit()
        return o.id


@pytest.fixture
def sku(app, org):
    with app.app_context():
        p = catalog.create_product(org, "Red bundle", skus=[{"code": "RED", "priceCents": 2500}])
        s = p.skus[0]
        inventory.move(s.id, MovementKind.restock, 10, source_type="test", source_id="seed")
        db.session.commit()
        return s.id


def level(app, sku_id):
    with app.app_context():
        s = db.session.get(Sku, sku_id)
        return s.on_hand, s.reserved, s.available


def test_reserve_commit_release_math(app, sku):
    with app.app_context():
        inventory.reserve(sku, 3, source_type="sale", source_id="1")
        inventory.reserve(sku, 2, source_type="sale", source_id="2")
        db.session.commit()
    assert level(app, sku) == (10, 5, 5)
    with app.app_context():
        inventory.commit(sku, 3, source_type="sale", source_id="1")  # paid
        inventory.release(sku, 2, source_type="sale", source_id="2")  # cancelled
        db.session.commit()
    assert level(app, sku) == (7, 0, 7)
    with app.app_context():
        s = db.session.get(Sku, sku)
        assert inventory.recompute(s) == (7, 0)


def test_replaying_the_same_event_moves_nothing(app, sku):
    with app.app_context():
        a = inventory.reserve(sku, 4, source_type="sale", source_id="9")
        b = inventory.reserve(sku, 4, source_type="sale", source_id="9")
        db.session.commit()
        assert a.id == b.id
    assert level(app, sku) == (10, 4, 6)


def test_overselling_goes_negative_and_says_how_many_to_buy(app, sku):
    with app.app_context():
        inventory.reserve(sku, 10, source_type="sale", source_id="all")
        inventory.reserve(sku, 3, source_type="sale", source_id="three-more")
        db.session.commit()
        s = db.session.get(Sku, sku)
        assert (s.on_hand, s.reserved, s.available, s.short) == (10, 13, -3, 3)
        # Paying for the oversold sale takes the shelf below zero: 3 owed to buyers.
        inventory.commit(sku, 13, source_type="sale", source_id="all-paid")
        db.session.commit()
        s = db.session.get(Sku, sku)
        assert (s.on_hand, s.reserved, s.available, s.short) == (-3, 0, -3, 3)
        # Buying 5 more fills the 3 owed and leaves 2 on the shelf.
        inventory.move(sku, MovementKind.restock, 5, source_type="test", source_id="buy")
        db.session.commit()
        s = db.session.get(Sku, sku)
        assert (s.on_hand, s.available, s.short) == (2, 2, 0)
        assert inventory.recompute(s) == (2, 0)


def test_counts_may_land_below_held_but_never_below_zero(app, sku):
    with app.app_context():
        inventory.reserve(sku, 6, source_type="sale", source_id="six")
        inventory.set_on_hand(sku, 4, source_type="count", source_id="c1", actor=Actor())
        db.session.commit()
        s = db.session.get(Sku, sku)
        assert (s.on_hand, s.reserved, s.short) == (4, 6, 2)
        with pytest.raises(InventoryError, match="below zero"):
            inventory.move(sku, MovementKind.adjust, -5)
        db.session.rollback()


def test_cannot_release_or_commit_more_than_reserved(app, sku):
    with app.app_context():
        inventory.reserve(sku, 10, source_type="sale", source_id="all")
        with pytest.raises(InventoryError):
            inventory.release(sku, 11, source_type="sale", source_id="all")
        with pytest.raises(InventoryError):
            inventory.commit(sku, 11, source_type="sale", source_id="all")
        db.session.rollback()
    assert level(app, sku) == (10, 0, 10)


def test_fifty_parallel_reserves_lose_no_updates(app, sku):
    """50 workers each reserve 1 of 10 on the shelf: all succeed (overselling is allowed),
    and the row lock keeps every one of them in the count: 50 held, 40 to buy."""
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(i: int):
        with app.app_context():
            try:
                inventory.reserve(sku, 1, source_type="sale", source_id=f"par-{i}")
                db.session.commit()
            except Exception as e:  # noqa: BLE001 - any failure fails the test below
                db.session.rollback()
                with lock:
                    errors.append(e)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert level(app, sku) == (10, 50, -40)
    with app.app_context():
        s = db.session.get(Sku, sku)
        assert s.short == 40
        assert inventory.recompute(s) == (10, 50)


def test_set_on_hand_is_one_adjust_and_noop_when_equal(app, sku):
    with app.app_context():
        assert (
            inventory.set_on_hand(sku, 10, source_type="import", source_id="x:1", actor=Actor())
            is None
        )
        m = inventory.set_on_hand(sku, 4, source_type="import", source_id="x:2", actor=Actor())
        db.session.commit()
        assert m.kind is MovementKind.adjust and m.quantity == -6
    assert level(app, sku) == (4, 0, 4)


# ---------- catalog ----------


def test_product_creates_default_sku_and_alias(app, org):
    with app.app_context():
        p = catalog.create_product(org, "Ultra Pack")
        db.session.commit()
        assert p.skus[0].code == "ULTRA-PACK"
        assert catalog.resolve_alias(org, "  ultra   PACK ").id == p.skus[0].id
        assert catalog.resolve_alias(org, "ultra-pack").id == p.skus[0].id
        assert catalog.resolve_alias(org, "nope") is None
        with pytest.raises(catalog.CatalogError, match="already exists"):
            catalog.create_product(org, "Ultra Pack")


def test_alias_cannot_point_to_two_skus(app, org):
    with app.app_context():
        a = catalog.create_product(org, "A")
        b = catalog.create_product(org, "B")
        catalog.add_alias(org, a.skus[0], "the thing")
        with pytest.raises(catalog.CatalogError, match="already points"):
            catalog.add_alias(org, b.skus[0], "The Thing")


SHEET = """Item,Start of Show Inventory,Current Stock
Red bundle,12,7
Ultra Pack,5,5
,3,3
Mega Pack,2,abc
Red bundle,1,1
"""


def test_parse_csv_uses_current_stock_and_flags_bad_rows():
    rows = catalog.parse_csv(SHEET)
    assert [(r.item, r.quantity, r.action, r.error) for r in rows] == [
        ("Red bundle", 7, "", None),
        ("Ultra Pack", 5, "", None),
        ("", 0, "skip", "no item name"),
        ("Mega Pack", 0, "skip", "bad quantity 'abc'"),
        ("Red bundle", 1, "", None),
    ]
    with pytest.raises(catalog.CatalogError, match="item column"):
        catalog.parse_csv("foo,bar\n1,2\n")


def test_import_plan_and_apply_are_idempotent(app, org, sku):
    with app.app_context():
        plan = catalog.plan_import(org, catalog.parse_csv(SHEET))
        assert [r.action for r in plan.rows] == ["set", "create", "skip", "skip", "skip"]
        assert plan.rows[0].before == 10 and plan.rows[4].error == "duplicate row"
        catalog.apply_import(org, plan, actor=Actor(), import_id="imp1")
        db.session.commit()
        assert catalog.resolve_alias(org, "red bundle").on_hand == 7
        assert catalog.resolve_alias(org, "ultra pack").on_hand == 5
        # applying the same plan again changes nothing (set rows are now 'unchanged', creates exist)
        plan2 = catalog.plan_import(org, catalog.parse_csv(SHEET))
        assert [r.action for r in plan2.rows][:2] == ["unchanged", "unchanged"]


def test_import_carries_the_sheets_negative_count_as_short(app, org, sku):
    sheet = "Item,Current Stock\nRed bundle,-2\nCloud Lights,-21\n"
    with app.app_context():
        rows = catalog.parse_csv(sheet)
        assert [(r.quantity, r.action) for r in rows] == [(-2, ""), (-21, "")]
        plan = catalog.plan_import(org, rows)
        assert [r.action for r in plan.rows] == ["set", "create"]
        catalog.apply_import(org, plan, actor=Actor(), import_id="neg1")
        db.session.commit()
        for name, owed in (("red bundle", 2), ("cloud lights", 21)):
            s = catalog.resolve_alias(org, name)
            assert (s.on_hand, s.short) == (-owed, owed)
            assert inventory.recompute(s) == (s.on_hand, s.reserved)
        # replaying the same sheet moves nothing
        again = catalog.plan_import(org, catalog.parse_csv(sheet))
        assert [r.action for r in again.rows] == ["unchanged", "unchanged"]
        # a hand count still may not go below zero
        with pytest.raises(InventoryError, match="below zero"):
            inventory.set_on_hand(sku, -5, source_type="count", source_id="c1", actor=Actor())


# ---------- routes ----------


def login_owner(client):
    r = client.get("/api/auth/csrf")
    h = {"X-CSRF-Token": r.get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "organization": "Shop",
            "name": "O",
            "email": "o@shop.test",
            "password": "correct-horse-battery",
        },
        headers=h,
    )
    return h


def test_inventory_routes_end_to_end(client, app):
    h = login_owner(client)
    r = client.post(
        "/api/products",
        json={"name": "Red bundle", "skus": [{"code": "RED", "priceCents": 2500}]},
        headers=h,
    )
    assert r.status_code == 201
    sku_id = r.get_json()["product"]["skus"][0]["id"]
    assert client.post("/api/products", json={"name": "Red bundle"}, headers=h).status_code == 409
    assert (
        client.post(f"/api/skus/{sku_id}/aliases", json={"alias": "red"}, headers=h).status_code
        == 201
    )
    r = client.post(
        f"/api/inventory/{sku_id}/adjust", json={"delta": 5, "reason": "count"}, headers=h
    )
    assert r.status_code == 200 and r.get_json()["sku"]["onHand"] == 5
    r = client.post(f"/api/inventory/{sku_id}/adjust", json={"setTo": 8}, headers=h)
    assert r.get_json()["sku"]["onHand"] == 8
    assert (
        client.post(f"/api/inventory/{sku_id}/adjust", json={"delta": "x"}, headers=h).status_code
        == 400
    )
    levels = client.get("/api/inventory").get_json()["skus"]
    assert levels[0]["productName"] == "Red bundle" and levels[0]["available"] == 8
    moves = client.get(f"/api/inventory/{sku_id}/movements").get_json()["movements"]
    assert [m["kind"] for m in moves] == ["adjust", "adjust"] and moves[0]["actor"] == "o@shop.test"
    r = client.post(
        "/api/inventory/import",
        json={"csv": "Item,Current Stock\nRed bundle,3\nNew thing,2\n"},
        headers=h,
    )
    assert r.get_json()["summary"] == {"set": 1, "create": 1} and r.get_json()["applied"] is False
    assert (
        client.get("/api/inventory").get_json()["skus"][0]["onHand"] == 8
    )  # preview changed nothing
    r = client.post(
        "/api/inventory/import",
        json={"csv": "Item,Current Stock\nRed bundle,3\nNew thing,2\n", "apply": True},
        headers=h,
    )
    assert r.get_json()["applied"] is True
    levels = {s["code"]: s["onHand"] for s in client.get("/api/inventory").get_json()["skus"]}
    assert levels == {"NEW-THING": 2, "RED": 3}
    actions = [e["action"] for e in client.get("/api/audit").get_json()["entries"]]
    assert actions[0] == "inventory.imported"


def test_viewer_can_read_but_not_write_inventory(app, client):
    h = login_owner(client)
    token = (
        client.post("/api/team/invites", json={"email": "v@shop.test", "role": "viewer"}, headers=h)
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    v = app.test_client()
    hv = {"X-CSRF-Token": v.get("/api/auth/csrf").get_json()["token"]}
    v.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "V", "password": "correct-horse-battery"},
        headers=hv,
    )
    assert v.get("/api/inventory").status_code == 200
    assert v.post("/api/products", json={"name": "X"}, headers=hv).status_code == 403

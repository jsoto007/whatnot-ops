"""GET /api/ingest/stock: what the scraper panel warns about (3, 2, 1 left, sold out)."""

from app.db import db
from app.models import MovementKind, Organization
from app.services import catalog, inventory
from app.services.devices import create_device
from tests.conftest import SHOW


def key_for(app, org_id):
    with app.app_context():
        _, key = create_device(db.session.get(Organization, org_id), "L")
        db.session.commit()
        return {"Authorization": "Bearer " + key}


def wins(client, auth, lines, start=1):
    rows = [
        {
            "id": f"{i:016x}",
            "showId": SHOW,
            "username": "",
            "body": b,
            "rawText": b,
            "capturedAt": SHOW,
            "seq": i,
        }
        for i, b in enumerate(lines, start=start)
    ]
    r = client.post(
        "/api/ingest",
        json={"version": 1, "show": {"id": SHOW, "startedAt": SHOW}, "rows": rows},
        headers=auth,
    )
    assert r.status_code == 200


def left(client, auth):
    r = client.get("/api/ingest/stock", headers=auth)
    assert r.status_code == 200
    body = r.get_json()
    assert body["alertAt"] == 3
    return {i["code"]: i["available"] for i in body["items"]}


def test_scraped_sales_count_the_item_down_to_sold_out(client, app, monkeypatch, world):
    monkeypatch.setenv("JOBS_INLINE", "1")  # the parse runs during the upload, as in e2e
    auth = key_for(app, world["org"])
    with app.app_context():
        big = catalog.create_product(world["org"], "Blue box", skus=[{"code": "BLUE"}]).skus[0]
        inventory.move(big.id, MovementKind.restock, 10, source_type="test", source_id="seed")
        gone = catalog.create_product(world["org"], "Old stock", skus=[{"code": "OLD"}]).skus[0]
        gone.active = False  # retired items never alert
        db.session.commit()

    assert left(client, auth) == {"RED": 3}  # 3 on hand in the `world` fixture
    wins(client, auth, ["lani808 won Red bundle"])
    assert left(client, auth) == {"RED": 2}
    wins(client, auth, ["kat.c won Red bundle", "pokemom_22 won Red bundle"], start=2)
    assert left(client, auth) == {"RED": 0}
    wins(client, auth, ["sam won Red bundle"], start=4)  # overselling is allowed
    assert left(client, auth) == {"RED": -1}
    wins(client, auth, ["cancelled for sam"], start=5)  # a cancel gives the unit back
    assert left(client, auth) == {"RED": 0}


def test_most_left_first_and_only_this_shop(client, app, world):
    auth = key_for(app, world["org"])
    with app.app_context():
        for code, qty in (("A", 1), ("B", 2), ("C", 0)):
            s = catalog.create_product(world["org"], f"Item {code}", skus=[{"code": code}]).skus[0]
            if qty:
                inventory.move(s.id, MovementKind.restock, qty, source_type="test", source_id=code)
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.flush()
        catalog.create_product(other.id, "Theirs", skus=[{"code": "THEIRS"}])
        db.session.commit()
    r = client.get("/api/ingest/stock", headers=auth)
    items = r.get_json()["items"]
    assert [i["code"] for i in items] == ["RED", "B", "A", "C"]
    assert items[0]["name"] == "Red bundle"


def test_stock_needs_a_device_key(client):
    assert client.get("/api/ingest/stock").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.get("/api/ingest/stock", headers=bad).status_code == 401

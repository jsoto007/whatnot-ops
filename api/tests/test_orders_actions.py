"""The Orders page acts on one lot at a time: every sale says which decision is waiting on it,
and a person may ask, in so many words, for the other lots with the same title to follow."""

from datetime import UTC, datetime

from sqlalchemy import select

from app.db import db
from app.models import ChatMessage, Order, ReviewItem, Show, User
from app.services import pipeline, rules
from tests.test_pipeline import open_reviews, run, sales

WIN = "{buyer} won the auction for {item} for {price}"


def win(buyer, item, price="$7.00"):
    return ("", WIN.format(buyer=buyer, item=item, price=price), "player")


def sign_in(client, app, org_id):
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "acceptLicence": True,
            "organization": "O",
            "name": "O",
            "email": "o@x.test",
            "password": "correct-horse",
        },
        headers=h,
    )
    with app.app_context():
        u = db.session.scalar(select(User))
        u.organization_id = org_id
        db.session.commit()
    return h


def by_buyer(client):
    return {o["buyer"]: o for o in client.get("/api/orders").get_json()["orders"]}


def test_item_title_drops_only_the_trailing_lot_number():
    assert rules.item_title("$1 BEAUTY PULLS #520") == "$1 BEAUTY PULLS"
    assert rules.item_title("$1 BEAUTY PULLS # 7") == "$1 BEAUTY PULLS"
    assert rules.item_title("Charizard #4 holo") == "Charizard #4 holo"  # not at the end
    assert rules.item_title("Red bundle") == "Red bundle"
    assert rules.item_title("") == ""


def test_each_sale_names_the_decision_waiting_on_it(client, app, world):
    world["say"](
        app,
        [
            win("moonlove3", "$1 BEAUTY PULLS #520"),
            win("lani808", "Red bundle #9", "$25.00"),
            ("", "payment failed for lani808"),
        ],
    )
    run(app, world["show"])
    h = sign_in(client, app, world["org"])
    orders = by_buyer(client)
    unknown = orders["moonlove3"]["sales"][0]
    assert orders["moonlove3"]["status"] == "open"
    assert unknown["itemTitle"] == "$1 BEAUTY PULLS" and unknown["lot"] == 520
    assert unknown["review"]["reason"] == "unmatched_item"
    declined = orders["lani808"]["sales"][0]
    assert declined["status"] == "payment_failed"
    assert declined["review"]["reason"] == "payment_declined"
    # The order page carries the same pointers.
    o = client.get(f"/api/orders/{orders['moonlove3']['id']}").get_json()["order"]
    assert o["sales"][0]["review"]["id"] == unknown["review"]["id"]
    # Settled, the pointer goes away.
    r = client.post(
        f"/api/review/{unknown['review']['id']}/resolve",
        json={"action": "assign_sku", "skuId": world["red"], "remember": False},
        headers=h,
    )
    assert r.status_code == 200 and r.get_json()["alsoResolved"] == 0
    orders = by_buyer(client)
    assert orders["moonlove3"]["status"] == "paid"
    assert orders["moonlove3"]["sales"][0]["review"] is None


def test_a_pick_lands_on_that_lot_only_unless_the_person_says_otherwise(client, app, world):
    world["say"](
        app,
        [
            win("moonlove3", "$1 BEAUTY PULLS #520"),
            win("taravat08", "$1 BEAUTY PULLS #521"),
            win("amyshopsalot", "$1 BEAUTY PULLS #522"),
            win("bailllip", "$5 BEAUTY PULLS #600"),
        ],
    )
    run(app, world["show"])
    assert [r for r, _ in open_reviews(app)] == ["unmatched_item"] * 4
    h = sign_in(client, app, world["org"])
    orders = by_buyer(client)
    moon = orders["moonlove3"]["sales"][0]["review"]["id"]
    # Default: only moonlove3's lot #520 gets the product.
    r = client.post(
        f"/api/review/{moon}/resolve",
        json={"action": "assign_sku", "skuId": world["red"], "remember": False},
        headers=h,
    )
    assert r.status_code == 200 and r.get_json()["alsoResolved"] == 0
    assert sorted(sales(app, world["show"])) == sorted(
        [
            ("moonlove3", "paid", "RED", 1),
            ("taravat08", "detected", None, 1),
            ("amyshopsalot", "detected", None, 1),
            ("bailllip", "detected", None, 1),
        ]
    )
    # Asked for: the other "$1 BEAUTY PULLS" lots follow; the "$5" title does not.
    tara = by_buyer(client)["taravat08"]["sales"][0]["review"]["id"]
    r = client.post(
        f"/api/review/{tara}/resolve",
        json={
            "action": "assign_sku",
            "skuId": world["red"],
            "remember": True,
            "applyToSimilar": True,
        },
        headers=h,
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["alsoResolved"] == 1
    assert sorted(sales(app, world["show"])) == sorted(
        [
            ("moonlove3", "paid", "RED", 1),
            ("taravat08", "paid", "RED", 1),
            ("amyshopsalot", "paid", "RED", 1),
            ("bailllip", "detected", None, 1),
        ]
    )
    assert [r for r, _ in open_reviews(app)] == ["unmatched_item"]
    orders = by_buyer(client)
    assert {b: o["status"] for b, o in orders.items()} == {
        "moonlove3": "paid",
        "taravat08": "paid",
        "amyshopsalot": "paid",
        "bailllip": "open",
    }
    # Every lot that moved is in the activity log, not just the one clicked.
    said = [e["summary"] for e in client.get("/api/audit").get_json()["entries"]]
    assert said.count("Resolved a review item") == 3
    # "Remember" learned each lot's exact title, lot number included: lot #761 may be
    # another bottle, so the rules never widen a learned name on their own.
    with app.app_context():
        from app.services import catalog

        assert catalog.resolve_alias(world["org"], "$1 BEAUTY PULLS #521").code == "RED"
        assert catalog.resolve_alias(world["org"], "$1 BEAUTY PULLS #522").code == "RED"
        assert catalog.resolve_alias(world["org"], "$1 BEAUTY PULLS #523") is None


def test_similar_lots_are_the_same_show_only(app, world):
    world["say"](app, [win("moonlove3", "$1 BEAUTY PULLS #520")])
    run(app, world["show"])
    with app.app_context():
        other = Show(
            organization_id=world["org"],
            show_key="other-show",
            started_at=datetime.now(UTC),
            show_date=datetime.now(UTC).date(),
        )
        db.session.add(other)
        db.session.flush()
        db.session.add(
            ChatMessage(
                show_id=other.id,
                client_id="other-1",
                seq=1,
                username="",
                body=WIN.format(buyer="pinkpony_22", item="$1 BEAUTY PULLS #533", price="$7.00"),
                raw_text="x",
                source="player",
                captured_at=datetime.now(UTC),
            )
        )
        db.session.commit()
        other_id = other.id
    run(app, other_id)
    assert [r for r, _ in open_reviews(app)] == ["unmatched_item", "unmatched_item"]
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.show_id == world["show"]))
        assert pipeline.similar_open_items(item) == []
        assert db.session.scalar(select(db.func.count()).select_from(Order)) == 2


def test_widening_reaches_only_the_lots_the_person_was_shown(client, app, world):
    world["say"](
        app,
        [
            win("moonlove3", "$1 BEAUTY PULLS #520"),
            win("taravat08", "$1 BEAUTY PULLS #521"),
            win("amyshopsalot", "$1 BEAUTY PULLS #522"),
            win("kels.finds", "$1 BEAUTY PULLS #523"),
        ],
    )
    run(app, world["show"])
    h = sign_in(client, app, world["org"])
    orders = by_buyer(client)
    rid = {b: o["sales"][0]["review"]["id"] for b, o in orders.items()}
    # The dialog listed taravat08 and amyshopsalot; kels.finds won after it opened. An id that
    # is not a similar open lot of this show (another org, a typo) is ignored, never resolved.
    r = client.post(
        f"/api/review/{rid['moonlove3']}/resolve",
        json={
            "action": "assign_sku",
            "skuId": world["red"],
            "remember": False,
            "applyTo": [rid["taravat08"], rid["amyshopsalot"], 999_999],
        },
        headers=h,
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["alsoResolved"] == 2
    assert sorted(sales(app, world["show"])) == sorted(
        [
            ("moonlove3", "paid", "RED", 1),
            ("taravat08", "paid", "RED", 1),
            ("amyshopsalot", "paid", "RED", 1),
            ("kels.finds", "detected", None, 1),
        ]
    )
    # Only a list of ids will do.
    r = client.post(
        f"/api/review/{rid['kels.finds']}/resolve",
        json={"action": "assign_sku", "skuId": world["red"], "applyTo": ["12"]},
        headers=h,
    )
    assert r.status_code == 400
    assert sales(app, world["show"])[-1] == ("kels.finds", "detected", None, 1)


def test_a_bulk_run_is_capped_and_changes_nothing_when_refused(client, app, world, monkeypatch):
    world["say"](
        app,
        [
            win("moonlove3", "$1 BEAUTY PULLS #520"),
            win("taravat08", "$1 BEAUTY PULLS #521"),
            win("amyshopsalot", "$1 BEAUTY PULLS #522"),
        ],
    )
    run(app, world["show"])
    h = sign_in(client, app, world["org"])
    moon = by_buyer(client)["moonlove3"]["sales"][0]["review"]["id"]
    monkeypatch.setattr(pipeline, "SIMILAR_LIMIT", 1)
    r = client.post(
        f"/api/review/{moon}/resolve",
        json={"action": "assign_sku", "skuId": world["red"], "applyToSimilar": True},
        headers=h,
    )
    assert r.status_code == 400 and "1 other lots" in r.get_json()["error"]
    # Refused as a whole: moonlove3's own pick did not land either.
    assert sales(app, world["show"]) == [
        ("moonlove3", "detected", None, 1),
        ("taravat08", "detected", None, 1),
        ("amyshopsalot", "detected", None, 1),
    ]
    assert [r for r, _ in open_reviews(app)] == ["unmatched_item"] * 3

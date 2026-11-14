"""A listing title that names one lot of one show is never learned as a product's name.

Lot numbers start again in every show. "Remember this name" used to save the whole title,
number included, so the next show's lot with that number was named, sold from stock and
marked paid with nobody looking. Every buyer here is made up.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import serializers
from app.db import db
from app.models import ChatMessage, ProductAlias, ReviewItem, ReviewStatus, Sale, Show
from app.services import catalog, pipeline, rules
from tests.test_pipeline import level, run
from tests.test_player_wins import owner
from tests.test_stock import owner_client

CHOOSE_ANY = "CHOOSE ANY PRODUCT YOU WANT #73"
PULLS = "$1 BEAUTY PULLS #520"


@pytest.mark.parametrize(
    "title,lot",
    [
        (PULLS, True),
        (f"  {PULLS}  ", True),
        (f"{PULLS}!", True),
        (f"{PULLS} 🔥", True),
        (CHOOSE_ANY, True),
        ("CHOOSE ANY PRODUCT YOU WANT", True),  # no number, but the winner chooses
        ("Your choice", True),
        ("Quick Hit 12", True),
        ("Ultra 19", True),
        ("Red bundle #752", True),
        ("#73", True),
        # The number may be the product, but a title cannot say so: typed on the product's page.
        ("Charizard #4", True),
        ("Red bundle", False),
        ("Ultra pack", False),
        ("Ultra 30 Sunscreen", False),
        ("mystery box", False),
        ("Lipstick No. 5", False),
        ("Pick-Up Sticks", False),
        # The product comes after the number: the title names it, so it is learned.
        ("Lot 5 - Pikachu", False),
        ("Mystery Box #12 (x2)", False),
        ("", False),
    ],
)
def test_which_titles_name_a_lot(title, lot):
    assert rules.names_a_lot(title) is lot


def _win(app, show_id, buyer, title, seq):
    """A win as the extension reads it from Whatnot's video player."""
    body = f"{buyer} won the auction for {title} for $7.00"
    with app.app_context():
        db.session.add(
            ChatMessage(
                show_id=show_id,
                client_id=f"{seq:016x}",
                seq=seq,
                username="",
                body=body,
                raw_text=body,
                source="player",
                captured_at=datetime.now(UTC),
            )
        )
        db.session.commit()


def _next_show(app, org_id):
    with app.app_context():
        show = Show(
            organization_id=org_id,
            show_key="2026-10-20T20:00:00.000Z",
            started_at=datetime.now(UTC),
            show_date=(datetime.now(UTC) + timedelta(days=1)).date(),
        )
        db.session.add(show)
        db.session.commit()
        return show.id


def _name_it(app, world, *, remember=True):
    """What Review sends when a person picks the product for the one open item."""
    with app.app_context():
        item = db.session.scalar(
            select(ReviewItem).where(
                ReviewItem.reason == "unmatched_item", ReviewItem.status == ReviewStatus.open
            )
        )
        pipeline.resolve(
            item,
            "assign_sku",
            {"skuId": world["red"], "remember": remember},
            user=owner(world["org"]),
        )
        db.session.commit()


def _sale(app, show_id):
    with app.app_context():
        s = db.session.scalar(select(Sale).where(Sale.show_id == show_id))
        return s.buyer_username, s.sku_id, s.status.value


def _aliases(app):
    with app.app_context():
        return set(db.session.scalars(select(ProductAlias.alias)))


@pytest.mark.parametrize("title", [PULLS, CHOOSE_ANY, "Quick Hit 12"])
def test_remembering_a_lot_title_does_not_name_the_next_shows_lot(app, world, title):
    before = _aliases(app)
    _win(app, world["show"], "buyer_one", title, 1)
    run(app, world["show"])
    _name_it(app, world, remember=True)
    # This win got its product and its stock moved, as before.
    assert _sale(app, world["show"]) == ("buyer_one", world["red"], "paid")
    assert level(app, world["red"]) == (2, 0)
    # Nothing was learned from the title.
    assert _aliases(app) == before

    # The next show's lot with the same title and number is another product: it waits for a
    # person, unpaid, and no stock moves.
    nxt = _next_show(app, world["org"])
    _win(app, nxt, "buyer_two", title, 2)
    run(app, nxt)
    assert _sale(app, nxt) == ("buyer_two", None, "detected")
    assert level(app, world["red"]) == (2, 0)
    with app.app_context():
        waiting = db.session.scalars(
            select(ReviewItem.reason).where(
                ReviewItem.show_id == nxt, ReviewItem.status == ReviewStatus.open
            )
        ).all()
        assert waiting == ["unmatched_item"]


def test_a_title_that_names_a_product_is_still_remembered(app, world):
    _win(app, world["show"], "buyer_one", "Mystery Pull Bag", 1)
    run(app, world["show"])
    _name_it(app, world, remember=True)
    assert "mystery pull bag" in _aliases(app)
    nxt = _next_show(app, world["org"])
    _win(app, nxt, "buyer_two", "Mystery Pull Bag", 2)
    run(app, nxt)
    assert _sale(app, nxt) == ("buyer_two", world["red"], "paid")


def test_a_name_saved_before_the_fix_cannot_name_a_winner_chooses_listing(app, world):
    """Names like this may already be stored. The title says the winner chooses, so no saved
    name decides it: not for a win read from the player, not for one made from an order."""
    with app.app_context():
        red = catalog.get_sku(world["org"], world["red"])
        stored = catalog.norm_alias(CHOOSE_ANY)
        db.session.add(ProductAlias(organization_id=world["org"], sku=red, alias=stored))
        db.session.commit()
        assert pipeline.match_sku(world["org"], CHOOSE_ANY) is None

    _win(app, world["show"], "buyer_one", CHOOSE_ANY, 1)
    run(app, world["show"])
    assert _sale(app, world["show"]) == ("buyer_one", None, "detected")

    with app.app_context():
        nxt = _next_show(app, world["org"])
        sale = pipeline.create_sale_from_order(
            db.session.get(Show, nxt),
            buyer="buyer_two",
            item_text=CHOOSE_ANY,
            quantity=1,
            price_cents=700,
            external_order_id="1000000001",
            ordered_at=datetime.now(UTC),
            settled=True,
        )
        db.session.commit()
        assert (sale.sku_id, sale.status.value) == (None, "detected")
    assert level(app, world["red"]) == (3, 0)


def test_the_sale_says_whether_its_name_can_be_remembered(app, world):
    _win(app, world["show"], "buyer_one", PULLS, 1)
    _win(app, world["show"], "buyer_two", "Mystery Pull Bag", 2)
    run(app, world["show"])
    with app.app_context():
        by_buyer = {
            s.buyer_username: serializers.sale(s)["rememberName"]
            for s in db.session.scalars(select(Sale))
        }
    assert by_buyer == {"buyer_one": False, "buyer_two": True}


def test_typing_a_name_on_the_products_page(client):
    h = owner_client(client)
    sku_id = client.post(
        "/api/products", json={"name": "Glow serum", "skus": [{"code": "GLOW"}]}, headers=h
    ).get_json()["product"]["skus"][0]["id"]

    # A title that says the winner chooses is refused, in words.
    r = client.post(f"/api/skus/{sku_id}/aliases", json={"alias": CHOOSE_ANY}, headers=h)
    assert r.status_code == 400
    assert "winner picks the product" in r.get_json()["error"]

    # A name with a number is the person's call here: the number can be the product.
    r = client.post(f"/api/skus/{sku_id}/aliases", json={"alias": "Charizard #4"}, headers=h)
    assert r.status_code == 201

    # A product whose own name reads that way is still created; its name is just not a
    # chat name (it could never match).
    r = client.post("/api/products", json={"name": "Pick your poison"}, headers=h)
    assert r.status_code == 201
    names = {
        a["alias"] for s in client.get("/api/inventory").get_json()["skus"] for a in s["aliases"]
    }
    assert "charizard #4" in names
    assert "pick your poison" not in names

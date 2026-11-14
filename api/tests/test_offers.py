"""Special offers (stage 5): a win holds one product unless the host or a trusted moderator
says more in chat ("#45 is two for one", "#45 bogo"), or a person sets the count on the
sale. The pick worker then takes that many of the winner's spots.

A viewer's line never sets an offer. Every buyer, moderator and lot here is made up.
"""

import pytest
from sqlalchemy import select

from app.db import db
from app.models import AuditLog, Organization, ReviewItem, ReviewStatus, Sale, Show
from app.services import activity, offers, pipeline, rules, sale_items
from tests import test_pick_worker as pw
from tests.test_pick_worker import found, sales
from tests.test_pipeline import level

shop = pw.shop
stage = pw.stage


@pytest.mark.parametrize(
    "line,said",
    [
        ("#45 is two for one", (45, 2)),
        ("#45 bogo", (45, 2)),
        ("lot 45 2 for 1", (45, 2)),
        ("bogo on #45", (45, 2)),
        ("#45 3 for 1!!", (45, 3)),
        ("quick hit 12 buy one get one", (12, 2)),
        ("#45 and #46 bogo", None),  # which lot?
        ("two for one today", None),  # no lot
        ("#45 looks nice", None),
        ("#45 1 for 1", None),
        ("#45 is not bogo", (45, 1)),  # taken back: the lot holds one again
        ("no bogo on #45 sorry", (45, 1)),
        ("#45 isn't two for one", (45, 1)),
        ("is #45 bogo?", None),  # a question says nothing
        ("#45 2 for 1.50", None),  # a price
        ("#45 2 for 1 dollar", None),
        ("#45 2 for 1 USD", None),
        ("#45 2 for 1 . 50", None),
        ("#45 bogo？", None),  # a full-width question mark
        # the lot named is not the lot on offer
        ("#1 sold! next one is bogo", None),
        ("next one after #45 is bogo", None),
        ("#45 sold, bogo next", None),
        ("congrats on #45, bogo coming up", None),
        ("thanks for #45! 2 for 1 on the next", None),
        ("bogo ends at #45", None),
        ("#45 was a great one, everything else is bogo", None),
        ("#45 9 for 1", None),  # more than a win ever holds
        ("", None),
    ],
)
def test_what_an_offer_line_says(line, said):
    assert rules.offer(line) == said


def products(app):
    with app.app_context():
        return [
            [(p["sku"].code, p["quantity"]) for p in sale_items.products(s)]
            for s in db.session.scalars(select(Sale).order_by(Sale.id))
        ]


def test_a_two_for_one_takes_both_spots_from_one_line(app, stage):
    stage["say"]("host_shop", "#1 is two for one", 9)
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4 C3", 10.5)
    assert stage["go"]() == {"assigned": 1}
    assert sales(app) == [("ann_made_up", "paid", "RED")]
    assert products(app) == [[("RED", 1), ("BLUE", 1)]]
    assert (level(app, stage["red"]), level(app, stage["blue"])) == ((2, 0), (-1, 0))
    assert stage["go"]() == {}  # reading again moves nothing
    assert (level(app, stage["red"]), level(app, stage["blue"])) == ((2, 0), (-1, 0))
    # one slip, a row per product, the price on the first only (as on the paper sheet)
    with app.app_context():
        from app.services import order_slips

        (slip,) = order_slips.build(db.session.get(Show, stage["show"]))["slips"]
        rows = [(r["product"], r["priceCents"] is not None, r["continued"]) for r in slip["rows"]]
        assert rows == [("Red bundle", True, False), ("Blue bundle", False, True)]


def test_both_spots_can_come_in_two_lines_and_the_offer_after_the_win(app, stage):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["say"]("host_shop", "bogo on #1", 11)
    stage["say"]("ann_made_up", "and B4 please", 12)  # two of the same
    assert stage["go"]() == {"assigned": 1}
    assert products(app) == [[("RED", 1), ("RED", 1)]]
    assert level(app, stage["red"]) == (1, 0)


def test_a_trusted_moderator_can_set_an_offer_and_a_viewer_cannot(app, stage):
    with app.app_context():
        db.session.get(Organization, stage["org"]).trusted_mods = ["mod_made_up"]
        db.session.commit()
    stage["say"]("Mod_Made_Up", "#1 bogo", 9)  # the name's case does not matter
    stage["say"]("viewer9", "#2 bogo", 9.1)  # anyone can type this
    stage["say"]("Kod_made_up", "#2 bogo", 9.2)  # a look-alike of nobody on the list
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4 C3", 10.5)
    stage["win"]("bo_made_up", 2, minute=12)
    stage["say"]("bo_made_up", "B4 C3", 12.5)
    assert stage["go"]() == {"assigned": 1, "several": 1, "changed": 1}
    assert products(app) == [[("RED", 1), ("BLUE", 1)], []]
    assert found(app) == [{"state": "several", "spots": ["B4", "C3"]}]


def test_fewer_spots_than_the_offer_allows_is_left_for_a_person(app, stage):
    stage["say"]("host_shop", "#1 is two for one", 9)
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    assert stage["go"]() == {"suggested": 1, "changed": 1}
    (pick,) = found(app)
    assert (pick["spot"], pick["allowed"], pick["held"]) == ("B4", 2, ["short"])
    assert level(app, stage["red"]) == (3, 0)


def test_a_person_can_allow_two_products_on_one_sale(app, client, stage, sign_in):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4 C3", 10.5)
    h = sign_in(stage["org"], "manager")
    with app.app_context():
        sale_id = db.session.scalar(select(Sale.id))
    url = f"/api/orders/sales/{sale_id}/allowed"
    assert client.patch(url, json={"products": 9}, headers=h).status_code == 400
    assert client.patch(url, json={"products": True}, headers=h).status_code == 400
    r = client.patch(url, json={"products": 2}, headers=h)
    assert r.status_code == 200 and r.get_json()["productsAllowed"] == 2
    assert stage["go"]() == {"assigned": 1}
    assert products(app) == [[("RED", 1), ("BLUE", 1)]]
    with app.app_context():
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "sale.products_allowed")
        )
        assert activity.describe(entry.action, entry.details) == (
            "Set ann_made_up's sale to hold up to 2 products"
        )
    viewer = sign_in(stage["org"], "viewer")
    assert client.patch(url, json={"products": 2}, headers=viewer).status_code == 403


def test_with_the_switch_off_one_press_applies_both_picks(app, stage):
    with app.app_context():
        db.session.get(Organization, stage["org"]).auto_pick = False
        db.session.commit()
    stage["say"]("host_shop", "#1 bogo", 9)
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4 C3", 10.5)
    assert stage["go"]() == {"suggested": 1, "changed": 1}
    (pick,) = found(app)
    assert pick["extra"] == [{"spot": "C3", "skuId": stage["blue"], "name": "Blue bundle"}]
    with app.app_context():
        item = db.session.scalar(select(ReviewItem).where(ReviewItem.status == ReviewStatus.open))
        pipeline.resolve(item, "apply_proposal", {}, user=None, actor=pipeline.RULE_ACTOR)
        db.session.commit()
    assert products(app) == [[("RED", 1), ("BLUE", 1)]]
    assert (level(app, stage["red"]), level(app, stage["blue"])) == ((2, 0), (-1, 0))


def test_the_latest_word_on_a_lot_stands_and_only_for_its_show(app, stage):
    stage["say"]("host_shop", "#1 bogo", 8)
    stage["say"]("host_shop", "#1 3 for 1", 9)
    stage["say"]("host_shop", "#7 bogo", 41)  # after the show ended
    with app.app_context():
        show = db.session.get(Show, stage["show"])
        org = db.session.get(Organization, stage["org"])
        assert offers.of_show([show.id], org, until=stage["end"]) == {1: 3}
        assert offers.of_show([show.id], org) == {1: 3, 7: 2}
        assert offers.of_show([], org) == {}


def test_the_owner_keeps_the_list_of_trusted_moderators(app, client, world, sign_in):
    h = sign_in(world["org"])
    assert client.get("/api/auth/me").get_json()["organization"]["trustedMods"] == []
    r = client.patch(
        "/api/team/organization",
        json={"trustedMods": ["@Mod_One", " mod.two ", "mod_one"]},
        headers=h,
    )
    assert r.status_code == 200
    assert r.get_json()["organization"]["trustedMods"] == ["mod_one", "mod.two"]
    bad = client.patch("/api/team/organization", json={"trustedMods": ["not a name"]}, headers=h)
    assert bad.status_code == 400
    assert (
        client.patch("/api/team/organization", json={"trustedMods": "x"}, headers=h).status_code
        == 400
    )
    with app.app_context():
        entry = db.session.scalars(
            select(AuditLog)
            .where(AuditLog.action == "organization.updated")
            .order_by(AuditLog.id.desc())
        ).first()
        assert activity.describe(entry.action, entry.details) == (
            "Set the trusted moderators (2 names)"
        )


# ---------- found by the stage's adversarial review ----------


def test_a_line_after_a_second_win_is_not_put_on_the_first_wins_spare_slot(app, stage):
    """ann has a two-for-one with one pick, wins another lot and types a spot: the second
    product of the first, or the pick of the second? Nothing of hers is assigned by itself."""
    stage["say"]("host_shop", "#1 bogo", 9)
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["win"]("ann_made_up", 2, minute=12)
    stage["say"]("ann_made_up", "C3", 12.5)
    assert stage["go"]() == {"suggested": 2, "changed": 2}
    assert products(app) == [[], []]
    assert (level(app, stage["red"]), level(app, stage["blue"])) == ((3, 0), (0, 0))
    first, second = found(app)
    assert (first["spot"], sorted(first["held"])) == ("B4", ["short", "unclear"])
    assert (second["spot"], second["held"]) == ("C3", ["unclear"])


def test_the_host_can_take_an_offer_back_and_a_person_can_overrule_it(app, client, stage, sign_in):
    stage["say"]("host_shop", "#1 bogo", 9)
    stage["say"]("host_shop", "#1 is not bogo sorry", 9.5)
    stage["say"]("host_shop", "#2 bogo", 9.6)
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4 C3", 10.5)
    stage["win"]("bea_made_up", 2, minute=11)
    stage["say"]("bea_made_up", "B4 C3", 11.5)
    h = sign_in(stage["org"], "manager")
    with app.app_context():
        bea = db.session.scalar(select(Sale.id).where(Sale.buyer_username == "bea_made_up"))
    url = f"/api/orders/sales/{bea}/allowed"
    # a body that is not an object is refused, never a crash
    assert client.patch(url, json=[2], headers=h).status_code == 400
    r = client.patch(url, json={"products": 1}, headers=h)
    assert r.status_code == 200 and r.get_json()["productsAllowed"] == 1
    # both wins hold one, and each winner typed two spots: none is chosen
    stage["go"]()
    assert products(app) == [[], []]
    assert [p["state"] for p in found(app)] == ["several", "several"]
    # back to what the chat says: bea's lot is a two for one again
    r = client.patch(url, json={"products": None}, headers=h)
    assert r.status_code == 200 and r.get_json()["productsAllowed"] is None
    stage["go"]()
    assert products(app) == [[], [("RED", 1), ("BLUE", 1)]]


def test_applying_a_two_for_one_whose_second_product_was_archived_changes_nothing(
    app, client, stage, sign_in
):
    with app.app_context():
        db.session.get(Organization, stage["org"]).auto_pick = False
        db.session.commit()
    stage["say"]("host_shop", "#1 bogo", 9)
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4 C3", 10.5)
    stage["go"]()
    with app.app_context():
        from app.models import Sku

        db.session.get(Sku, stage["blue"]).active = False
        db.session.commit()
        item = db.session.scalar(
            select(ReviewItem.id).where(ReviewItem.status == ReviewStatus.open)
        )
    h = sign_in(stage["org"], "manager")
    r = client.post(f"/api/review/{item}/resolve", json={"action": "apply_proposal"}, headers=h)
    assert r.status_code == 400 and "no longer in use" in r.get_json()["error"]
    assert products(app) == [[]]
    assert (level(app, stage["red"]), level(app, stage["blue"])) == ((3, 0), (0, 0))


def test_a_line_typed_as_the_next_lot_is_being_won_is_not_put_on_the_two_for_one(app, stage):
    """zed's two for one has one pick. A spot typed seconds before the next win is recorded
    may be for either: nothing of zed's is assigned by itself."""
    stage["say"]("host_shop", "#1 bogo", 9)
    stage["win"]("zed_made_up", 1, minute=10)
    stage["say"]("zed_made_up", "B4", 10.2)
    stage["say"]("zed_made_up", "C3", 11.8)
    stage["win"]("zed_made_up", 2, minute=12)
    stage["say"]("zed_made_up", "B4", 12.5)
    assert "assigned" not in stage["go"]()
    assert products(app) == [[], []]
    assert (level(app, stage["red"]), level(app, stage["blue"])) == ((3, 0), (0, 0))


def test_an_extra_product_in_a_suggestion_must_be_a_real_product_of_the_shop(app, stage):
    from app.services import picks

    stage["win"]("ann_made_up", 1, minute=10)
    with app.app_context():
        sale = db.session.scalar(select(Sale))
        for bad in (True, 10**20, -1, "2", None, 2.0):
            assert picks.extras_usable(sale, {"extra": [{"skuId": bad}]}) is False
        assert picks.extras_usable(sale, {"extra": "abc"}) is False
        assert picks.extras_usable(sale, {"extra": [{"skuId": stage["blue"]}]}) is True


def test_a_product_number_too_large_to_be_one_is_refused_not_an_error(app, client, stage, sign_in):
    stage["win"]("ann_made_up", 1, minute=10)
    stage["say"]("ann_made_up", "B4", 10.5)
    stage["go"]()
    h = sign_in(stage["org"], "manager")
    with app.app_context():
        sale_id = db.session.scalar(select(Sale.id))
    for big in (2**31, 10**20):
        r = client.patch(f"/api/orders/sales/{sale_id}/product", json={"skuId": big}, headers=h)
        assert r.status_code == 400
        r = client.post(f"/api/orders/sales/{sale_id}/products", json={"skuId": big}, headers=h)
        assert r.status_code == 400
    assert products(app) == [[("RED", 1)]]

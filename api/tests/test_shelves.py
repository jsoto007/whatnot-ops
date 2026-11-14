"""A show's shelf layout ("B4" = shelf B, spot 4) and the product it suggests in Review for a
win waiting for its product. Names here are made up."""

from sqlalchemy import select

from app.db import db
from app.models import AuditLog, Organization, Show
from app.services import activity, catalog
from tests.test_pick_chat import won
from tests.test_pipeline import run
from tests.test_sale_chat import say_at


def _layout(world, app):
    with app.app_context():
        catalog.create_product(world["org"], "Blue bundle", skus=[{"code": "BLU-1"}])
        db.session.commit()


def test_a_manager_lays_out_the_shelves_and_everyone_reads_them(app, client, world, sign_in):
    _layout(world, app)
    h = sign_in(world["org"], "manager")
    url = f"/api/shows/{world['show']}/shelves"
    body = {"shelves": [{"spot": "a10", "code": "red"}, {"spot": "A02", "code": "BLU-1"}]}
    r = client.post(url, json=body, headers=h)
    assert r.status_code == 200, r.get_json()
    spots = [(s["spot"], s["code"], s["name"]) for s in r.get_json()["shelves"]]
    assert spots == [("A2", "BLU-1", "Blue bundle"), ("A10", "RED", "Red bundle")]
    # Saving again replaces the layout; it never touches the show's last-chat time.
    with app.app_context():
        before = db.session.get(Show, world["show"]).updated_at
    r = client.post(url, json={"shelves": [{"spot": "B4", "code": "RED"}]}, headers=h)
    assert [s["spot"] for s in client.get(url).get_json()["shelves"]] == ["B4"]
    with app.app_context():
        assert db.session.get(Show, world["show"]).updated_at == before
        log = db.session.scalars(select(AuditLog).where(AuditLog.action == "show.shelves")).all()
        assert [a.details for a in log] == [{"spots": 2}, {"spots": 1}]
        assert (
            activity.describe("show.shelves", log[-1].details)
            == "Laid out 1 shelf spots for a show"
        )


def test_a_layout_is_all_or_nothing_and_says_what_is_wrong(app, client, world, sign_in):
    with app.app_context():
        old = catalog.create_product(world["org"], "Old bundle", skus=[{"code": "OLD"}]).skus[0]
        old.active = False
        other = Organization(name="Other", slug="other-shop")
        db.session.add(other)
        db.session.flush()
        catalog.create_product(other.id, "Their bundle", skus=[{"code": "THEIRS"}])
        db.session.commit()
    h = sign_in(world["org"], "manager")
    url = f"/api/shows/{world['show']}/shelves"
    assert (
        client.post(url, json={"shelves": [{"spot": "B4", "code": "RED"}]}, headers=h).status_code
        == 200
    )
    for shelves, words in [
        ([{"spot": "B", "code": "RED"}], "not a shelf spot"),
        ([{"spot": "B4", "code": "RED"}, {"spot": "b4", "code": "RED"}], "B4 is listed twice"),
        (
            [{"spot": "C1", "code": "RED"}, {"spot": "C2", "code": "NOPE"}],
            "No product in use has the code NOPE",
        ),
        ([{"spot": "C1"}], "list of spot and product code"),
        ([{"spot": "C1", "code": " "}], "C1 needs a product code"),
        ([{"spot": "C1", "code": "OLD"}], "No product in use has the code OLD"),  # archived
        ([{"spot": "C1", "code": "THEIRS"}], "No product in use has the code THEIRS"),
    ]:
        r = client.post(url, json={"shelves": shelves}, headers=h)
        assert r.status_code == 400 and words in r.get_json()["error"]
    assert [s["spot"] for s in client.get(url).get_json()["shelves"]] == ["B4"]


def test_only_people_who_settle_review_lay_out_shelves(app, client, world, sign_in):
    h = sign_in(world["org"], "fulfillment")
    url = f"/api/shows/{world['show']}/shelves"
    assert client.get(url).status_code == 200
    assert client.post(url, json={"shelves": []}, headers=h).status_code == 403


def test_review_suggests_the_product_on_the_spot_the_winner_typed(app, client, world, sign_in):
    _layout(world, app)
    h = sign_in(world["org"], "manager")
    show = world["show"]
    say_at(
        app,
        show,
        [
            (0, *won("lani808", 11)),
            (1, "lani808", "b4 please"),
            (2, *won("kat.c", 12)),
            (3, "kat.c", "A2 or B4"),  # two spots: no suggestion
            (4, "4x4_deals", "B4"),  # not a winner of either lot
            (5, *won("buyer77", 13)),
            (6, "buyer77", "b04"),
            (7, "buyer77", "actually C9"),  # a spot not laid out: no longer B4
            (8, *won("jess88", 14)),
            (9, "jess88", "B04 thanks"),
        ],
    )
    run(app, show)
    shelves = [{"spot": "B4", "code": "BLU-1"}, {"spot": "A2", "code": "RED"}]
    assert (
        client.post(f"/api/shows/{show}/shelves", json={"shelves": shelves}, headers=h).status_code
        == 200
    )
    items = client.get("/api/review").get_json()["items"]
    by_buyer = {i["sale"]["buyer"]: i for i in items if i["reason"] == "unmatched_item"}
    assert by_buyer["lani808"]["shelf"]["spot"] == "B4"
    assert by_buyer["lani808"]["shelf"]["name"] == "Blue bundle"
    assert by_buyer["kat.c"]["shelf"] is None
    assert by_buyer["buyer77"]["shelf"] is None
    assert by_buyer["jess88"]["shelf"]["spot"] == "B4"


# ---------- copy from the last show; a winner's-pick show with no shelves ----------


def _another_show(world, app, key="2026-10-20T20:00:00.000Z", day=20):
    from datetime import UTC, date, datetime

    with app.app_context():
        show = Show(
            organization_id=world["org"],
            show_key=key,
            started_at=datetime(2026, 10, day, 20, tzinfo=UTC),
            show_date=date(2026, 10, day),
        )
        db.session.add(show)
        db.session.commit()
        return show.id


def test_a_new_show_copies_the_last_shows_shelves(app, client, world, sign_in):
    _layout(world, app)
    h = sign_in(world["org"], "manager")
    first = f"/api/shows/{world['show']}/shelves"
    body = {"shelves": [{"spot": "A1", "code": "RED"}, {"spot": "B4", "code": "BLU-1"}]}
    assert client.post(first, json=body, headers=h).status_code == 200
    new = _another_show(world, app)
    url = f"/api/shows/{new}/shelves"
    got = client.get(url).get_json()
    assert got == {"shelves": [], "canCopy": True}
    # A winner's-pick show with no shelves says so on its page.
    assert client.get(f"/api/shows/{new}").get_json()["noShelves"] is True
    r = client.post(url + "/copy", headers=h)
    assert r.status_code == 200
    assert [(s["spot"], s["code"]) for s in r.get_json()["shelves"]] == [
        ("A1", "RED"),
        ("B4", "BLU-1"),
    ]
    assert client.get(url).get_json()["canCopy"] is False
    assert client.get(f"/api/shows/{new}").get_json()["noShelves"] is False
    # Never over a layout that is there.
    r = client.post(url + "/copy", headers=h)
    assert r.status_code == 400 and "already" in r.get_json()["error"]
    with app.app_context():
        last = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "show.shelves").order_by(AuditLog.id.desc())
        ).first()
        assert activity.describe("show.shelves", last.details) == (
            "Laid out 2 shelf spots for a show, copied from the last show"
        )


def test_copying_needs_an_earlier_layout_and_the_right_to_change_shelves(
    app, client, world, sign_in
):
    h = sign_in(world["org"], "manager")
    url = f"/api/shows/{world['show']}/shelves"
    assert client.get(url).get_json()["canCopy"] is False
    r = client.post(url + "/copy", headers=h)
    assert r.status_code == 400 and r.get_json()["error"] == "No earlier show has shelves to copy."
    viewer = sign_in(world["org"], "viewer")
    assert client.post(url + "/copy", headers=viewer).status_code == 403


def test_a_show_that_sells_one_product_per_listing_is_not_told_it_has_no_shelves(
    app, client, world, sign_in
):
    h = sign_in(world["org"])
    assert client.get(f"/api/shows/{world['show']}").get_json()["noShelves"] is True
    client.patch("/api/team/organization", json={"saleMode": "listing"}, headers=h)
    assert client.get(f"/api/shows/{world['show']}").get_json()["noShelves"] is False

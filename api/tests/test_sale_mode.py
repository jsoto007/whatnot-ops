"""How winners get their product, show by show: the shop's usual way and a show's own choice
(services/sale_mode.py). Every buyer and shop here is made up."""

from datetime import UTC, datetime

from sqlalchemy import select

from app import serializers
from app.db import db
from app.models import (
    ChatMessage,
    Organization,
    Sale,
    Show,
    ShowSaleMode,
    WhatnotConnection,
    WhatnotShow,
)
from app.services import sale_mode
from tests.test_pipeline import run


def _show(client, show_id):
    return client.get(f"/api/shows/{show_id}").get_json()["show"]


def _mode(client, show_id):
    s = _show(client, show_id)
    return s["saleMode"], s["saleModeSet"]


def _activity(client):
    return [e["summary"] for e in client.get("/api/audit").get_json()["entries"]]


def _recording(app, org_id, key, whatnot_show_id=None):
    with app.app_context():
        show = Show(
            organization_id=org_id,
            show_key=key,
            started_at=datetime.now(UTC),
            show_date=datetime.now(UTC).date(),
            whatnot_show_id=whatnot_show_id,
        )
        db.session.add(show)
        db.session.commit()
        return show.id


def test_a_shop_starts_as_winners_pick_and_its_shows_follow_it(app, client, world, sign_in):
    sign_in(world["org"])
    assert client.get("/api/auth/me").get_json()["organization"]["saleMode"] == "winners_pick"
    assert _mode(client, world["show"]) == ("winners_pick", False)
    listed = client.get("/api/shows").get_json()["shows"]
    assert [(s["saleMode"], s["saleModeSet"]) for s in listed] == [("winners_pick", False)]


def test_the_owner_sets_the_shops_usual_way_and_shows_follow(app, client, world, sign_in):
    h = sign_in(world["org"])
    r = client.patch("/api/team/organization", json={"saleMode": "listing"}, headers=h)
    assert r.status_code == 200 and r.get_json()["organization"]["saleMode"] == "listing"
    assert _mode(client, world["show"]) == ("listing", False)
    assert "Set how the shop usually sells to “one product per listing”" in _activity(client)

    # Saying the same again changes nothing and logs nothing.
    client.patch("/api/team/organization", json={"saleMode": "listing"}, headers=h)
    assert sum("how the shop usually sells" in s for s in _activity(client)) == 1

    # Anything else is refused in words, and nothing changes.
    for bad in ["", "auto", None, 3, ["listing"]]:
        r = client.patch("/api/team/organization", json={"saleMode": bad}, headers=h)
        assert r.status_code == 400, bad
        assert "winners pick in chat" in r.get_json()["error"]
    assert client.get("/api/auth/me").get_json()["organization"]["saleMode"] == "listing"


def test_only_the_owner_sets_the_shops_usual_way(app, client, world, sign_in):
    h = sign_in(world["org"], role="manager")
    r = client.patch("/api/team/organization", json={"saleMode": "listing"}, headers=h)
    assert r.status_code == 403
    with app.app_context():
        assert db.session.get(Organization, world["org"]).sale_mode == "winners_pick"


def test_a_show_can_sell_differently_and_go_back(app, client, world, sign_in):
    h = sign_in(world["org"], role="manager")
    with app.app_context():
        seen = db.session.get(Show, world["show"]).updated_at
    r = client.post(f"/api/shows/{world['show']}/mode", json={"mode": "listing"}, headers=h)
    assert r.status_code == 200
    assert (r.get_json()["show"]["saleMode"], r.get_json()["show"]["saleModeSet"]) == (
        "listing",
        True,
    )
    assert _mode(client, world["show"]) == ("listing", True)
    assert "Set how a show sells to “one product per listing”" in _activity(client)

    # The show keeps its own way when the shop's usual way changes under it.
    with app.app_context():
        db.session.get(Organization, world["org"]).sale_mode = "listing"
        db.session.commit()
    client.post(f"/api/shows/{world['show']}/mode", json={"mode": "winners_pick"}, headers=h)
    assert _mode(client, world["show"]) == ("winners_pick", True)

    # Back to the shop's usual way.
    r = client.post(f"/api/shows/{world['show']}/mode", json={"mode": None}, headers=h)
    assert r.status_code == 200
    assert _mode(client, world["show"]) == ("listing", False)
    assert (
        "Set a show back to the shop's usual way of selling (one product per listing)"
        in _activity(client)
    )
    with app.app_context():
        assert db.session.scalar(select(ShowSaleMode)) is None
        # Chat last arrived when it did: choosing a mode is not chat.
        assert db.session.get(Show, world["show"]).updated_at == seen


def test_a_shows_mode_is_checked_and_guarded(app, client, world, sign_in):
    h = sign_in(world["org"], role="manager")
    url = f"/api/shows/{world['show']}/mode"
    assert client.post(url, json={}, headers=h).status_code == 400
    for bad in ["", "auto", 1, ["listing"]]:
        r = client.post(url, json={"mode": bad}, headers=h)
        assert r.status_code == 400, bad
    assert _mode(client, world["show"]) == ("winners_pick", False)

    # Another shop's show is not found.
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    theirs = _recording(app, other_id, "2026-10-21T20:00:00.000Z")
    assert (
        client.post(f"/api/shows/{theirs}/mode", json={"mode": "listing"}, headers=h).status_code
        == 404
    )
    with app.app_context():
        assert db.session.scalar(select(ShowSaleMode)) is None


def test_people_who_only_pack_or_look_cannot_change_a_shows_mode(app, client, world, sign_in):
    for role in ("fulfillment", "viewer"):
        h = sign_in(world["org"], role=role, email=f"{role}@x.test")
        r = client.post(f"/api/shows/{world['show']}/mode", json={"mode": "listing"}, headers=h)
        assert r.status_code == 403, role
        client.post("/api/auth/logout", headers=h)
    with app.app_context():
        assert db.session.scalar(select(ShowSaleMode)) is None


def test_every_recording_of_one_whatnot_show_sells_the_same_way(app, world):
    """Two laptops, or a recording started again, are one show on Whatnot."""
    with app.app_context():
        ws = WhatnotShow(organization_id=world["org"], whatnot_id="ws-1", status="live")
        db.session.add(ws)
        db.session.flush()
        first = db.session.get(Show, world["show"])
        first.whatnot_show_id = ws.id
        db.session.commit()
        ws_id = ws.id
    second = _recording(app, world["org"], "2026-10-20T20:05:00.000Z", ws_id)
    alone = _recording(app, world["org"], "2026-10-22T20:00:00.000Z")

    with app.app_context():
        get = lambda i: db.session.get(Show, i)  # noqa: E731
        assert sale_mode.set_show(get(second), "listing") == ("winners_pick", "listing")
        db.session.commit()
        assert sale_mode.of_show(get(world["show"])) == ("listing", True)
        assert sale_mode.of_show(get(second)) == ("listing", True)
        # A show that is not that Whatnot show is untouched.
        assert sale_mode.of_show(get(alone)) == ("winners_pick", False)

    # A recording that appears later (the laptop restarted) follows the others.
    third = _recording(app, world["org"], "2026-10-20T20:30:00.000Z", ws_id)
    with app.app_context():
        assert sale_mode.of_show(db.session.get(Show, third)) == ("listing", True)
        shows = db.session.scalars(select(Show).order_by(Show.id)).all()
        assert sale_mode.of_shows(list(shows)) == {
            world["show"]: ("listing", True),
            second: ("listing", True),
            alone: ("winners_pick", False),
            third: ("listing", True),
        }
        # Going back on any of them goes back on all.
        assert sale_mode.set_show(db.session.get(Show, third), None) == (
            "listing",
            "winners_pick",
        )
        db.session.commit()
        assert db.session.scalar(select(ShowSaleMode)) is None
        assert sale_mode.of_show(db.session.get(Show, world["show"])) == ("winners_pick", False)


def test_a_sale_says_how_its_show_sells(app, client, world, sign_in):
    body = "buyer_one won the auction for Mystery Pull Bag for $7.00"
    with app.app_context():
        db.session.add(
            ChatMessage(
                show_id=world["show"],
                client_id=f"{1:016x}",
                seq=1,
                username="",
                body=body,
                raw_text=body,
                source="player",
                captured_at=datetime.now(UTC),
            )
        )
        db.session.commit()
    run(app, world["show"])
    with app.app_context():
        assert serializers.sale(db.session.scalar(select(Sale)))["saleMode"] == "winners_pick"
    h = sign_in(world["org"])
    assert [i["sale"]["saleMode"] for i in client.get("/api/review").get_json()["items"]] == [
        "winners_pick"
    ]
    client.post(f"/api/shows/{world['show']}/mode", json={"mode": "listing"}, headers=h)
    assert [i["sale"]["saleMode"] for i in client.get("/api/review").get_json()["items"]] == [
        "listing"
    ]


def test_the_laptop_is_told_what_the_show_needs_from_it(app, client, shop):
    from tests.conftest import SHOW

    def asks(key=SHOW):
        query = f"?show={key}" if key else ""
        return client.get(f"/api/ingest/stock{query}", headers=shop["auth"]).get_json()

    # Not connected to Whatnot: the laptop reads the winners itself.
    assert asks()["show"] == {"saleMode": "winners_pick", "winnersFromWhatnot": False}

    with app.app_context():
        conn = WhatnotConnection(organization_id=shop["org"], seller_id="1234567")
        db.session.add(conn)
        db.session.commit()
    # Connected, but orders are only compared: still the laptop.
    assert asks()["show"]["winnersFromWhatnot"] is False

    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        sale_mode.set_show(db.session.get(Show, shop["show"]), "listing")
        db.session.commit()
    assert asks()["show"] == {"saleMode": "listing", "winnersFromWhatnot": True}

    # A show the app has not seen, or no show named (an older extension): nothing is said.
    assert "show" not in asks("2026-01-01T00:00:00.000Z")
    assert "show" not in asks(None)
    assert set(asks(None)) == {"alertAt", "items"}


def test_recordings_that_chose_differently_before_they_were_matched_still_agree(app, world):
    """Each recording had its own choice; then both were matched to one Whatnot show. Every
    recording answers with the oldest one's choice, and the next choice rewrites them all."""
    second = _recording(app, world["org"], "2026-10-20T20:05:00.000Z")
    with app.app_context():
        get = lambda i: db.session.get(Show, i)  # noqa: E731
        sale_mode.set_show(get(world["show"]), "listing")
        sale_mode.set_show(get(second), "winners_pick")
        ws = WhatnotShow(organization_id=world["org"], whatnot_id="ws-2", status="live")
        db.session.add(ws)
        db.session.flush()
        get(world["show"]).whatnot_show_id = ws.id
        get(second).whatnot_show_id = ws.id
        db.session.commit()
        assert sale_mode.of_show(get(world["show"])) == ("listing", True)
        assert sale_mode.of_show(get(second)) == ("listing", True)

        assert sale_mode.set_show(get(second), "winners_pick") == ("listing", "winners_pick")
        db.session.commit()
        rows = db.session.execute(select(ShowSaleMode.show_id, ShowSaleMode.mode)).all()
        assert sorted(rows) == [(world["show"], "winners_pick"), (second, "winners_pick")]


def test_a_list_of_sales_from_many_shows_asks_once(app, world):
    """A page of orders can span dozens of shows: their modes are looked up together."""
    from sqlalchemy import event

    ids = [world["show"]] + [
        _recording(app, world["org"], f"2026-10-2{n}T20:00:00.000Z") for n in range(1, 6)
    ]
    with app.test_request_context():
        sale_mode.prime(ids)
        asked = []

        def note(conn, cursor, statement, *rest):
            asked.append(statement)

        event.listen(db.engine, "before_cursor_execute", note)
        try:
            assert {sale_mode.of_show_id(i) for i in ids} == {"winners_pick"}
        finally:
            event.remove(db.engine, "before_cursor_execute", note)
        assert asked == []


def test_a_body_that_is_not_an_object_is_refused_not_a_crash(app, client, world, sign_in):
    h = sign_in(world["org"])
    for body in ['"mode"', "[1]", "3", "null"]:
        r = client.post(
            f"/api/shows/{world['show']}/mode",
            data=body,
            headers={**h, "Content-Type": "application/json"},
        )
        assert r.status_code == 400, body
        r = client.patch(
            "/api/team/organization",
            data=body.replace("mode", "saleMode"),
            headers={**h, "Content-Type": "application/json"},
        )
        assert r.status_code == 200, body  # nothing to change
    assert client.get("/api/auth/me").get_json()["organization"]["saleMode"] == "winners_pick"
    assert _mode(client, world["show"]) == ("winners_pick", False)

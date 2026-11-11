"""Settings → Whatnot: connect, check, disconnect, and who may do which."""

from sqlalchemy import select

from app.db import db
from app.models import AuditLog, Organization, WhatnotConnection
from app.services import activity
from app.services.whatnot import connection


def status(client):
    return client.get("/api/whatnot").get_json()


def test_a_server_with_no_token_says_so_and_cannot_connect(app, client, world, sign_in):
    h = sign_in(world["org"])
    s = status(client)
    assert s["configured"] is False and s["connected"] is False and s["username"] is None
    r = client.post("/api/whatnot/connect", headers=h)
    assert r.status_code == 409
    assert "not set up" in r.get_json()["error"] and "TOKEN" not in r.get_json()["error"]


def test_the_owner_connects_the_shop_and_sees_who_it_is(app, client, world, sign_in, fake_whatnot):
    h = sign_in(world["org"])
    assert status(client) == {
        "configured": True,
        "connected": False,
        "username": None,
        "connectedAt": None,
        "lastCheckedAt": None,
        "problem": None,
        "webhooks": {"configured": False, "url": None, "lastEventAt": None},
        "sync": [],
    }
    r = client.post("/api/whatnot/connect", headers=h)
    assert r.status_code == 200
    s = r.get_json()
    assert s["connected"] is True and s["username"] == "host_shop" and s["problem"] is None
    assert s["connectedAt"] and s["lastCheckedAt"] and s["webhooks"]["waiting"] == 0
    with app.app_context():
        conn = db.session.scalar(select(WhatnotConnection))
        assert conn.seller_id == "1234567" and conn.currency == "USD" and conn.active
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "whatnot.connected"))
        assert activity.describe(entry.action, entry.details) == (
            "Connected the shop to Whatnot as @host_shop"
        )
    # Nothing secret or internal reaches the page.
    assert "wn_pat" not in r.get_data(as_text=True) and "1234567" not in r.get_data(as_text=True)


def test_connecting_fills_an_empty_host_username_but_never_replaces_one(
    app, client, world, sign_in, fake_whatnot
):
    h = sign_in(world["org"])
    with app.app_context():
        db.session.get(Organization, world["org"]).host_username = None
        db.session.commit()
    fake_whatnot.seller["username"] = "think_electronics"
    client.post("/api/whatnot/connect", headers=h)
    with app.app_context():
        assert db.session.get(Organization, world["org"]).host_username == "think_electronics"
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "whatnot.connected"))
        assert "set it as the host's username" in activity.describe(entry.action, entry.details)
        db.session.get(Organization, world["org"]).host_username = "typed_by_owner"
        db.session.commit()
    client.post("/api/whatnot/disconnect", headers=h)
    client.post("/api/whatnot/connect", headers=h)
    with app.app_context():
        assert db.session.get(Organization, world["org"]).host_username == "typed_by_owner"


def test_only_the_owner_connects_and_managers_only_look(app, client, world, sign_in, fake_whatnot):
    h = sign_in(world["org"], role="manager")
    assert client.get("/api/whatnot").status_code == 200
    assert client.post("/api/whatnot/connect", headers=h).status_code == 403
    assert client.post("/api/whatnot/disconnect", headers=h).status_code == 403
    assert fake_whatnot.calls == []


def test_fulfillment_and_viewers_do_not_see_the_connection(app, client, world, sign_in):
    sign_in(world["org"], role="fulfillment")
    assert client.get("/api/whatnot").status_code == 403


def test_writes_need_the_csrf_token(app, client, world, sign_in, fake_whatnot):
    sign_in(world["org"])
    assert client.post("/api/whatnot/connect").status_code == 403


def test_a_rejected_token_is_shown_as_a_problem_until_a_check_works(
    app, client, world, sign_in, fake_whatnot
):
    h = sign_in(world["org"])
    client.post("/api/whatnot/connect", headers=h)
    fake_whatnot.token = "regenerated"  # the owner regenerated it in Seller Hub
    s = client.post("/api/whatnot/check", headers=h).get_json()
    assert s["connected"] is True and s["problem"] == "token_rejected"
    assert client.get("/api/dashboard/counts").get_json()["whatnotProblem"] is True
    fake_whatnot.token = "wn_pat_tk_test"
    s = client.post("/api/whatnot/check", headers=h).get_json()
    assert s["problem"] is None
    assert client.get("/api/dashboard/counts").get_json()["whatnotProblem"] is False


def test_whatnot_being_down_is_a_problem_in_words_not_a_crash(
    app, client, world, sign_in, fake_whatnot
):
    h = sign_in(world["org"])
    for _ in range(4):
        fake_whatnot.fail_next(503, "service-unavailable")
    r = client.post("/api/whatnot/connect", headers=h)
    assert r.status_code == 502 and "could not be reached" in r.get_json()["error"]
    client.post("/api/whatnot/connect", headers=h)
    for _ in range(4):
        fake_whatnot.fail_next(503, "service-unavailable")
    assert client.post("/api/whatnot/check", headers=h).get_json()["problem"] == "unreachable"


def test_a_token_for_another_account_does_not_take_over_the_shop(
    app, client, world, sign_in, fake_whatnot
):
    h = sign_in(world["org"])
    client.post("/api/whatnot/connect", headers=h)
    fake_whatnot.seller.update(id="999", username="someone_else")
    assert client.post("/api/whatnot/check", headers=h).get_json()["problem"] == "token_rejected"
    client.post("/api/whatnot/disconnect", headers=h)
    r = client.post("/api/whatnot/connect", headers=h)
    assert r.status_code == 409 and "@host_shop before" in r.get_json()["error"]


def test_one_whatnot_account_belongs_to_one_shop(app, client, world, sign_in, fake_whatnot):
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.flush()
        db.session.add(WhatnotConnection(organization_id=other.id, seller_id="1234567"))
        db.session.commit()
    h = sign_in(world["org"])
    r = client.post("/api/whatnot/connect", headers=h)
    assert r.status_code == 409 and "another shop" in r.get_json()["error"]


def test_disconnect_stops_the_client_and_is_logged(app, client, world, sign_in, fake_whatnot):
    import pytest

    from app.services.whatnot import NotConnected, client_for

    h = sign_in(world["org"])
    with app.app_context():
        with pytest.raises(NotConnected):
            client_for(world["org"])
    client.post("/api/whatnot/connect", headers=h)
    with app.app_context():
        assert client_for(world["org"]).me()["username"] == "host_shop"
    s = client.post("/api/whatnot/disconnect", headers=h).get_json()
    assert s["connected"] is False and s["problem"] is None
    with app.app_context():
        with pytest.raises(NotConnected):
            client_for(world["org"])
        assert connection.by_seller("1234567") is None
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "whatnot.disconnected"))
        assert activity.describe(entry.action, entry.details) == (
            "Disconnected the shop from Whatnot (@host_shop)"
        )


def test_whatnot_as_an_actor_reads_as_whatnot_in_the_activity_log():
    assert activity.actor("whatnot", "") == "Whatnot"
    assert activity.who("order.status", "whatnot", None) == "Whatnot"

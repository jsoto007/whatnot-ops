import re
from unittest.mock import MagicMock

from sqlalchemy import select

from app.db import db
from app.models import AuditLog, Role, User
from app.services import mail

PW = "correct-horse-battery"


def csrf(client):
    r = client.get("/api/auth/csrf")
    return {"X-CSRF-Token": r.get_json()["token"]}


def signup(client, email="owner@shop.test", org="My Shop", name="Olive Owner", password=PW):
    h = csrf(client)
    return client.post(
        "/api/auth/signup",
        json={"organization": org, "name": name, "email": email, "password": password},
        headers=h,
    ), h


# ---------- sign-up / login / session ----------


def test_signup_creates_org_owner_and_session(client, app):
    r, h = signup(client)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["user"]["role"] == "owner" and body["organization"]["slug"] == "my-shop"
    assert "team.manage" in body["permissions"]
    assert client.get("/api/auth/me").status_code == 200
    with app.app_context():
        assert (
            db.session.scalar(select(AuditLog).where(AuditLog.action == "org.created")) is not None
        )


def test_signup_validates_input(client):
    h = csrf(client)
    r = client.post(
        "/api/auth/signup",
        json={"organization": "x", "name": "y", "email": "nope", "password": PW},
        headers=h,
    )
    assert r.status_code == 400 and "email" in r.get_json()["error"]
    r = client.post(
        "/api/auth/signup",
        json={"organization": "x", "name": "y", "email": "a@b.co", "password": "short"},
        headers=h,
    )
    assert r.status_code == 400 and "10 characters" in r.get_json()["error"]


def test_duplicate_email_is_409(client):
    signup(client)
    r, _ = signup(client, org="Other")
    assert r.status_code == 409


def test_writes_without_csrf_are_403(client):
    r = client.post("/api/auth/login", json={"email": "a@b.co", "password": PW})
    assert r.status_code == 403
    assert "CSRF" in r.get_json()["error"]


def test_me_without_session_is_401_and_says_whether_signups_are_open(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.get_json()["signupsOpen"] is True


def test_login_logout(client):
    signup(client)
    h = csrf(client)
    assert client.post("/api/auth/logout", headers=h).status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "OWNER@shop.test", "password": PW}, headers=h)
    assert r.status_code == 200 and r.get_json()["user"]["email"] == "owner@shop.test"
    assert client.get("/api/auth/me").status_code == 200


def test_failed_logins_are_limited_per_address_not_per_account(client, app):
    signup(client)
    h = csrf(client)
    client.post("/api/auth/logout", headers=h)
    for _ in range(8):
        r = client.post(
            "/api/auth/login",
            json={"email": "owner@shop.test", "password": "wrong-wrong-wrong"},
            headers=h,
        )
        assert r.status_code == 401
    # The guesser's address is now refused, even with the right password...
    r = client.post("/api/auth/login", json={"email": "owner@shop.test", "password": PW}, headers=h)
    assert r.status_code == 429
    # ...and the answer for an unknown account is the same (no enumeration).
    for _ in range(8):
        client.post(
            "/api/auth/login", json={"email": "nobody@x.test", "password": "x" * 12}, headers=h
        )
    r = client.post("/api/auth/login", json={"email": "nobody@x.test", "password": PW}, headers=h)
    assert r.status_code == 429
    # The real owner, elsewhere, is not locked out.
    owner = app.test_client()
    ho = csrf(owner)
    r = owner.post(
        "/api/auth/login",
        json={"email": "owner@shop.test", "password": PW},
        headers=ho,
        environ_overrides={"REMOTE_ADDR": "203.0.113.7"},
    )
    assert r.status_code == 200
    with app.app_context():
        assert db.session.scalar(select(AuditLog).where(AuditLog.action == "login.failed"))


def test_logout_everywhere_invalidates_other_sessions(app):
    c1, c2 = app.test_client(), app.test_client()
    signup(c1)
    h2 = csrf(c2)
    c2.post("/api/auth/login", json={"email": "owner@shop.test", "password": PW}, headers=h2)
    assert c2.get("/api/auth/me").status_code == 200
    c1.post("/api/auth/logout-everywhere", headers=csrf(c1))
    assert c2.get("/api/auth/me").status_code == 401


# ---------- invites ----------


def invite(client, email, role, h=None):
    return client.post(
        "/api/team/invites", json={"email": email, "role": role}, headers=h or csrf(client)
    )


def test_owner_invites_manager_who_accepts(app, client):
    signup(client)
    r = invite(client, "mia@shop.test", "manager")
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["emailSent"] is False  # Mailgun unset → logged, dev link returned
    token = body["acceptUrl"].rsplit("/", 1)[1]

    other = app.test_client()
    info = other.get("/api/auth/invites/" + token)
    assert info.get_json() == {
        "email": "mia@shop.test",
        "role": "manager",
        "organization": "My Shop",
    }
    r = other.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "Mia", "password": PW},
        headers=csrf(other),
    )
    assert r.status_code == 201 and r.get_json()["user"]["role"] == "manager"
    assert "team.manage" not in r.get_json()["permissions"]
    # token is single-use
    assert other.get("/api/auth/invites/" + token).status_code == 404
    team = client.get("/api/team").get_json()
    assert [u["email"] for u in team["users"]] == ["owner@shop.test", "mia@shop.test"]
    assert team["invites"] == []


def test_manager_cannot_invite_a_manager_but_can_invite_fulfillment(app, client):
    signup(client)
    token = invite(client, "mia@shop.test", "manager").get_json()["acceptUrl"].rsplit("/", 1)[1]
    mgr = app.test_client()
    mgr.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "Mia", "password": PW},
        headers=csrf(mgr),
    )
    assert mgr.get("/api/team").get_json()["invitableRoles"] == ["fulfillment", "viewer"]
    assert invite(mgr, "x@shop.test", "manager").status_code == 403
    assert invite(mgr, "x@shop.test", "fulfillment").status_code == 201


def test_fulfillment_cannot_see_team_or_settings(app, client):
    signup(client)
    token = invite(client, "pat@shop.test", "fulfillment").get_json()["acceptUrl"].rsplit("/", 1)[1]
    pat = app.test_client()
    pat.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "Pat", "password": PW},
        headers=csrf(pat),
    )
    assert pat.get("/api/team").status_code == 403
    assert pat.get("/api/devices").status_code == 403
    assert pat.get("/api/audit").status_code == 403
    assert pat.get("/api/shows").status_code == 200


def test_invite_can_be_revoked_and_replaced(app, client):
    signup(client)
    first = invite(client, "mia@shop.test", "viewer").get_json()
    second = invite(client, "mia@shop.test", "viewer").get_json()  # replaces the first
    assert (
        app.test_client()
        .get("/api/auth/invites/" + first["acceptUrl"].rsplit("/", 1)[1])
        .status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/team/invites/{second['invite']['id']}", headers=csrf(client)
        ).status_code
        == 204
    )
    assert client.get("/api/team").get_json()["invites"] == []


def test_invite_sends_through_mailgun_when_configured(client, monkeypatch):
    signup(client)
    monkeypatch.setenv("MAILGUN_API_KEY", "key-test")
    monkeypatch.setenv("MAILGUN_DOMAIN", "mg.example.com")
    post = MagicMock(return_value=MagicMock(status_code=200))
    monkeypatch.setattr(mail.requests, "post", post)
    r = invite(client, "mia@shop.test", "viewer")
    assert r.status_code == 201 and r.get_json()["emailSent"] is True
    assert "acceptUrl" not in r.get_json()
    url, kwargs = post.call_args[0][0], post.call_args[1]
    assert url == "https://api.mailgun.net/v3/mg.example.com/messages"
    assert kwargs["auth"] == ("api", "key-test")
    assert kwargs["data"]["to"] == "mia@shop.test"
    assert kwargs["data"]["subject"] == "Olive Owner invited you to join My Shop on Sales Ops"
    link = re.search(r"https?://\S+/invite/[A-Za-z0-9_-]{20,}", kwargs["data"]["text"])
    assert link
    assert link.group(0) in kwargs["data"]["html"]  # HTML twin carries the same link
    assert "/logo.png" in kwargs["data"]["html"]


# ---------- team management ----------


def test_owner_can_change_roles_and_disable_but_not_self_or_last_owner(app, client):
    signup(client)
    token = invite(client, "mia@shop.test", "viewer").get_json()["acceptUrl"].rsplit("/", 1)[1]
    mia = app.test_client()
    mia.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "Mia", "password": PW},
        headers=csrf(mia),
    )
    with app.app_context():
        owner_id = db.session.scalar(select(User.id).where(User.email == "owner@shop.test"))
        mia_id = db.session.scalar(select(User.id).where(User.email == "mia@shop.test"))
    h = csrf(client)
    assert (
        client.patch(f"/api/team/users/{owner_id}", json={"role": "viewer"}, headers=h).status_code
        == 409
    )
    assert (
        client.patch(f"/api/team/users/{owner_id}", json={"disabled": True}, headers=h).status_code
        == 409
    )
    r = client.patch(f"/api/team/users/{mia_id}", json={"role": "manager"}, headers=h)
    assert r.status_code == 200 and r.get_json()["role"] == "manager"
    assert mia.get("/api/auth/me").status_code == 401  # role change ends her sessions
    assert (
        client.patch(f"/api/team/users/{mia_id}", json={"disabled": True}, headers=h).get_json()[
            "disabled"
        ]
        is True
    )
    assert (
        mia.post(
            "/api/auth/login", json={"email": "mia@shop.test", "password": PW}, headers=csrf(mia)
        ).status_code
        == 401
    )
    # the manager cannot use team.manage
    assert (
        mia.patch(
            f"/api/team/users/{owner_id}", json={"role": "viewer"}, headers=csrf(mia)
        ).status_code
        == 401
    )


# ---------- devices from the UI ----------


def test_devices_are_created_once_shown_and_revocable(client):
    signup(client)
    h = csrf(client)
    r = client.post("/api/devices", json={"name": "Front laptop"}, headers=h)
    assert r.status_code == 201
    key = r.get_json()["key"]
    assert key.startswith("wn_")
    assert (
        client.get("/api/ingest/ping", headers={"Authorization": "Bearer " + key}).status_code
        == 200
    )
    devices = client.get("/api/devices").get_json()["devices"]
    assert devices[0]["keyPrefix"] == key[:10] and "key" not in devices[0]
    assert client.delete(f"/api/devices/{devices[0]['id']}", headers=h).status_code == 204
    assert (
        client.get("/api/ingest/ping", headers={"Authorization": "Bearer " + key}).status_code
        == 401
    )
    actions = [e["action"] for e in client.get("/api/audit").get_json()["entries"]]
    assert actions[:2] == ["device.revoked", "device.created"]


# ---------- password reset ----------


def test_password_reset_flow(client, app):
    signup(client)
    h = csrf(client)
    client.post("/api/auth/logout", headers=h)
    r = client.post("/api/auth/password/forgot", json={"email": "owner@shop.test"}, headers=h)
    assert r.status_code == 202
    token = r.get_json()["resetUrl"].rsplit("/", 1)[1]
    assert client.post(
        "/api/auth/password/forgot", json={"email": "nobody@shop.test"}, headers=h
    ).get_json() == {"ok": True}
    r = client.post(
        "/api/auth/password/reset",
        json={"token": token, "password": "a-brand-new-password"},
        headers=h,
    )
    assert r.status_code == 200
    assert (
        client.post(
            "/api/auth/password/reset",
            json={"token": token, "password": "another-new-password"},
            headers=h,
        ).status_code
        == 404
    )
    client.post("/api/auth/logout", headers=h)
    assert (
        client.post(
            "/api/auth/login", json={"email": "owner@shop.test", "password": PW}, headers=h
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "owner@shop.test", "password": "a-brand-new-password"},
            headers=h,
        ).status_code
        == 200
    )


# ---------- shows for signed-in users ----------


def test_shows_list_and_messages_are_scoped_to_the_org(client, app):
    signup(client)
    key = client.post("/api/devices", json={"name": "L"}, headers=csrf(client)).get_json()["key"]
    show = "2026-10-19T20:00:00.000Z"
    rows = [
        {
            "id": f"{i:016x}",
            "showId": show,
            "username": "lani808" if i == 2 else "u",
            "body": f"m{i}",
            "rawText": f"u m{i}",
            "capturedAt": show,
            "seq": i,
        }
        for i in range(1, 4)
    ]
    client.post(
        "/api/ingest",
        json={"version": 1, "show": {"id": show, "startedAt": show}, "rows": rows},
        headers={"Authorization": "Bearer " + key},
    )
    shows = client.get("/api/shows").get_json()["shows"]
    assert len(shows) == 1 and shows[0]["messageCount"] == 3 and shows[0]["date"] == "2026-10-19"
    msgs = client.get(f"/api/shows/{shows[0]['id']}/messages?q=M2").get_json()
    assert msgs["total"] == 1 and msgs["messages"][0]["username"] == "lani808"
    # another org cannot see it
    other = app.test_client()
    signup(other, email="o2@x.test", org="Other")
    assert other.get("/api/shows").get_json()["shows"] == []
    assert other.get(f"/api/shows/{shows[0]['id']}").status_code == 404


def test_production_without_admin_email_never_opens_signups(client, app, monkeypatch):
    app.config["APP_ENV"] = "production"
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    try:
        me = client.get("/api/auth/me").get_json()
        assert me["signupsOpen"] is False and me["signupAdminOnly"] is False
        r, _ = signup(client)  # even the very first visitor cannot claim the shop
        assert r.status_code == 403
    finally:
        app.config["APP_ENV"] = "test"


def test_only_the_admin_email_with_the_setup_code_can_create_the_shop_once(
    client, app, monkeypatch
):
    app.config["APP_ENV"] = "production"
    monkeypatch.setenv("ADMIN_EMAIL", "  Boss@Shop.test ")
    monkeypatch.delenv("SETUP_TOKEN", raising=False)
    try:
        # Production without SETUP_TOKEN: knowing the admin's email is not enough.
        assert client.get("/api/auth/me").get_json()["signupsOpen"] is False
        monkeypatch.setenv("SETUP_TOKEN", "s3t-up-t0ken-from-render")
        me = client.get("/api/auth/me").get_json()
        assert me["signupsOpen"] is True and me["signupAdminOnly"] is True
        assert me["setupCodeRequired"] is True
        r, _ = signup(client, email="first-visitor@x.test")
        assert r.status_code == 403 and "admin email" in r.get_json()["error"]
        r, _ = signup(client, email="boss@shop.test")  # right email, no code
        assert r.status_code == 403 and "setup code" in r.get_json()["error"]
        h = csrf(client)
        r = client.post(
            "/api/auth/signup",
            json={
                "organization": "My Shop",
                "name": "Boss",
                "email": "boss@shop.test",
                "password": PW,
                "setupCode": "wrong",
            },
            headers=h,
        )
        assert r.status_code == 403
        r = client.post(
            "/api/auth/signup",
            json={
                "organization": "My Shop",
                "name": "Boss",
                "email": "boss@shop.test",
                "password": PW,
                "setupCode": " s3t-up-t0ken-from-render ",
            },
            headers=h,
        )
        assert r.status_code == 201
        assert r.get_json()["user"]["role"] == "owner" and r.get_json()["user"]["isAdmin"] is True
        client.post("/api/auth/logout", headers=csrf(client))
        assert client.get("/api/auth/me").get_json()["signupsOpen"] is False
        r, _ = signup(client, email="boss@shop.test", org="Again")
        assert r.status_code == 403 and "already set up" in r.get_json()["error"]
    finally:
        app.config["APP_ENV"] = "test"


def test_the_admin_cannot_be_demoted_or_disabled_and_is_restored_on_login(client, app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "boss@shop.test")
    signup(client, email="boss@shop.test")
    h = csrf(client)
    token = (
        client.post("/api/team/invites", json={"email": "co@shop.test", "role": "owner"}, headers=h)
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    co = app.test_client()
    hc = csrf(co)
    co.post(
        "/api/auth/invites/accept", json={"token": token, "name": "Co", "password": PW}, headers=hc
    )
    users = {u["email"]: u for u in co.get("/api/team").get_json()["users"]}
    admin_id = users["boss@shop.test"]["id"]
    assert users["boss@shop.test"]["isAdmin"] is True and users["co@shop.test"]["isAdmin"] is False
    hc = csrf(co)
    r = co.patch(f"/api/team/users/{admin_id}", json={"role": "viewer"}, headers=hc)
    assert r.status_code == 409 and "ADMIN_EMAIL" in r.get_json()["error"]
    r = co.patch(f"/api/team/users/{admin_id}", json={"disabled": True}, headers=hc)
    assert r.status_code == 409
    # Even if the database says otherwise (an old demotion, a manual edit), logging in
    # as the ADMIN_EMAIL restores an enabled owner.
    with app.app_context():
        u = db.session.scalar(select(User).where(User.email == "boss@shop.test"))
        u.role = Role.viewer
        u.disabled = True
        db.session.commit()
    fresh = app.test_client()
    r = fresh.post(
        "/api/auth/login", json={"email": "boss@shop.test", "password": PW}, headers=csrf(fresh)
    )
    assert r.status_code == 200 and r.get_json()["user"]["role"] == "owner"
    actions = [e["action"] for e in fresh.get("/api/audit").get_json()["entries"]]
    assert "user.admin_restored" in actions


def test_an_invited_admin_email_becomes_owner(client, app, monkeypatch):
    signup(client)  # shop created before ADMIN_EMAIL was set
    monkeypatch.setenv("ADMIN_EMAIL", "new-boss@shop.test")
    h = csrf(client)
    token = (
        client.post(
            "/api/team/invites", json={"email": "new-boss@shop.test", "role": "viewer"}, headers=h
        )
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    b = app.test_client()
    r = b.post(
        "/api/auth/invites/accept",
        json={"token": token, "name": "New Boss", "password": PW},
        headers=csrf(b),
    )
    assert r.status_code == 201 and r.get_json()["user"]["role"] == "owner"


def test_owner_sets_the_host_username(client):
    signup(client)
    h = csrf(client)
    r = client.patch("/api/team/organization", json={"hostUsername": "@MyShopLive"}, headers=h)
    assert r.status_code == 200 and r.get_json()["organization"]["hostUsername"] == "MyShopLive"
    assert client.get("/api/auth/me").get_json()["organization"]["hostUsername"] == "MyShopLive"
    r = client.patch("/api/team/organization", json={"hostUsername": ""}, headers=h)
    assert r.get_json()["organization"]["hostUsername"] is None


# ---------- hardening (security audit 2026-10-20) ----------


def test_email_check_is_fast_on_hostile_input(client):
    import time

    h = csrf(client)
    started = time.monotonic()
    r = client.post(
        "/api/auth/password/forgot", json={"email": "a@" + "a." * 20000 + "@"}, headers=h
    )
    assert r.status_code == 202 and time.monotonic() - started < 0.5


def test_password_rules_reject_blank_and_huge(client):
    r, _ = signup(client, password=" " * 12)
    assert r.status_code == 400
    r, _ = signup(client, password="x" * 300)
    assert r.status_code == 400 and "at most" in r.get_json()["error"]


def test_logout_kills_a_copied_session_cookie(client, app):
    signup(client)
    stolen = app.test_client()
    stolen.set_cookie("wn_session", client.get_cookie("wn_session").value)
    assert stolen.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout", headers=csrf(client)).status_code == 204
    assert stolen.get("/api/auth/me").status_code == 401


def test_a_reset_kills_every_other_reset_link_and_refuses_disabled_users(client, app):
    signup(client)
    client.post("/api/auth/logout", headers=csrf(client))
    h = csrf(client)
    first = client.post("/api/auth/password/forgot", json={"email": "owner@shop.test"}, headers=h)
    second = client.post("/api/auth/password/forgot", json={"email": "owner@shop.test"}, headers=h)
    t1 = first.get_json()["resetUrl"].rsplit("/", 1)[1]
    t2 = second.get_json()["resetUrl"].rsplit("/", 1)[1]
    new = "brand-new-password-1"
    assert (
        client.post(
            "/api/auth/password/reset", json={"token": t1, "password": new}, headers=h
        ).status_code
        == 200
    )
    r = client.post("/api/auth/password/reset", json={"token": t2, "password": PW}, headers=h)
    assert r.status_code == 404  # the other link died with the first reset
    # a disabled person cannot use a link either
    third = client.post("/api/auth/password/forgot", json={"email": "owner@shop.test"}, headers=h)
    t3 = third.get_json()["resetUrl"].rsplit("/", 1)[1]
    with app.app_context():
        u = db.session.scalar(select(User).where(User.email == "owner@shop.test"))
        u.disabled = True
        db.session.commit()
    r = client.post("/api/auth/password/reset", json={"token": t3, "password": PW}, headers=h)
    assert r.status_code == 404


def test_reset_emails_and_signups_are_rate_limited(client, app):
    signup(client)
    client.post("/api/auth/logout", headers=csrf(client))
    h = csrf(client)
    links = [
        client.post("/api/auth/password/forgot", json={"email": "owner@shop.test"}, headers=h)
        .get_json()
        .get("resetUrl")
        for _ in range(5)
    ]
    assert sum(1 for x in links if x) == 3  # 3 per address per hour
    other = app.test_client()
    codes = [signup(other, email=f"s{i}@x.test", org=f"S{i}")[0].status_code for i in range(11)]
    assert codes.count(429) >= 1


def test_managers_cannot_touch_an_owners_invite_and_disabling_withdraws_grants(client, app):
    signup(client)
    h = csrf(client)

    def invite(c, hh, email, role):
        return c.post("/api/team/invites", json={"email": email, "role": role}, headers=hh)

    tok = invite(client, h, "mgr@shop.test", "manager").get_json()["acceptUrl"].rsplit("/", 1)[1]
    m = app.test_client()
    hm = csrf(m)
    m.post("/api/auth/invites/accept", json={"token": tok, "name": "M", "password": PW}, headers=hm)
    owner_inv = invite(client, h, "boss2@shop.test", "owner").get_json()
    assert invite(m, hm, "boss2@shop.test", "viewer").status_code == 409
    invites = client.get("/api/team").get_json()["invites"]
    owner_invite_id = next(i["id"] for i in invites if i["email"] == "boss2@shop.test")
    assert m.delete(f"/api/team/invites/{owner_invite_id}", headers=hm).status_code == 403
    assert owner_inv["acceptUrl"]

    # The manager opens an invite and a laptop code, then is disabled: both stop working.
    staff_tok = (
        invite(m, hm, "pat@shop.test", "fulfillment").get_json()["acceptUrl"].rsplit("/", 1)[1]
    )
    code = m.post("/api/devices/pairings", json={"name": "Lap"}, headers=hm).get_json()["code"]
    mgr_id = next(
        u["id"]
        for u in client.get("/api/team").get_json()["users"]
        if u["email"] == "mgr@shop.test"
    )
    assert (
        client.patch(f"/api/team/users/{mgr_id}", json={"disabled": True}, headers=h).status_code
        == 200
    )
    assert app.test_client().get(f"/api/auth/invites/{staff_tok}").status_code == 404
    assert app.test_client().post("/api/pair", json={"code": code}).status_code == 404

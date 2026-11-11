"""The shop's click to accept the Sales Ops License and Data Terms (services/licence.py)."""

import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from test_auth import PW, csrf, signup

from app.db import db
from app.models import AuditLog, LicenceAcceptance, Organization, User
from app.services import activity, licence

LICENSE_MD = Path(__file__).resolve().parents[2] / "LICENSE.md"


def rows(app) -> list[LicenceAcceptance]:
    with app.app_context():
        found = db.session.scalars(select(LicenceAcceptance).order_by(LicenceAcceptance.id)).all()
        db.session.expunge_all()
        return list(found)


def audit_rows(app) -> list[AuditLog]:
    with app.app_context():
        found = db.session.scalars(
            select(AuditLog).where(AuditLog.action == "licence.accepted")
        ).all()
        db.session.expunge_all()
        return list(found)


def old_shop(app, name="Old Shop") -> int:
    """A shop created before the licence step: no acceptance on record."""
    with app.app_context():
        org = Organization(name=name, slug=name.lower().replace(" ", "-"))
        db.session.add(org)
        db.session.commit()
        return org.id


def accept(client, h, version=licence.LICENCE_VERSION):
    return client.post("/api/auth/licence", json={"version": version}, headers=h)


def test_the_version_is_the_one_printed_in_license_md():
    head = LICENSE_MD.read_text(encoding="utf-8")[:2000]
    found = re.search(r"^Version (\S+) ·", head, re.MULTILINE)
    assert found, "LICENSE.md has no 'Version X ·' line near the top"
    assert found.group(1) == licence.LICENCE_VERSION


# ---------- sign-up ----------


@pytest.mark.parametrize("ticked", [None, False, "true", 1, "yes"])
def test_signup_refuses_without_the_box_ticked(client, app, ticked):
    h = csrf(client)
    body = {"organization": "My Shop", "name": "Olive", "email": "o@shop.test", "password": PW}
    if ticked is not None:
        body["acceptLicence"] = ticked
    r = client.post("/api/auth/signup", json=body, headers=h)
    assert r.status_code == 400
    assert "accept the Sales Ops License and Data Terms" in r.get_json()["error"]
    with app.app_context():
        assert db.session.scalar(select(func.count()).select_from(Organization)) == 0
        assert db.session.scalar(select(func.count()).select_from(User)) == 0
    assert rows(app) == []
    assert client.get("/api/auth/me").status_code == 401


def test_signup_records_the_shops_acceptance(client, app):
    before = datetime.now(UTC)
    r, _ = signup(client)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["licence"] == {"version": licence.LICENCE_VERSION, "needed": False}
    [row] = rows(app)
    user_id = r.get_json()["user"]["id"]
    assert row.organization_id == r.get_json()["organization"]["id"]
    assert row.version == licence.LICENCE_VERSION
    assert row.user_id == user_id
    assert row.client_ip == "127.0.0.1"
    assert row.accepted_at.tzinfo is not None
    assert before - timedelta(seconds=5) <= row.accepted_at <= datetime.now(UTC)
    [entry] = audit_rows(app)
    assert entry.actor_type == "user" and entry.actor_id == user_id
    assert entry.details == {"version": licence.LICENCE_VERSION, "ip": "127.0.0.1"}
    assert client.get("/api/auth/me").get_json()["licence"]["needed"] is False


def test_the_activity_log_says_it_in_words(client):
    r, h = signup(client)
    summaries = [e["summary"] for e in client.get("/api/audit").get_json()["entries"]]
    assert (
        f"Accepted the Sales Ops License and Data Terms, version {licence.LICENCE_VERSION} "
        "from 127.0.0.1"
    ) in summaries
    assert not any("licence" in s.lower() for s in summaries)
    # An older or odd details shape still reads as a sentence, never as the code.
    assert (
        activity.describe("licence.accepted", {}) == "Accepted the Sales Ops License and Data Terms"
    )


# ---------- a shop created before the licence step ----------


def test_an_existing_shop_asks_its_owner_until_they_accept(client, app, sign_in):
    org_id = old_shop(app)
    h = sign_in(org_id, "owner", email="boss@shop.test")
    me = client.get("/api/auth/me").get_json()
    assert me["licence"] == {"version": licence.LICENCE_VERSION, "needed": True}
    assert "licence.accept" in me["permissions"]

    r = accept(client, h)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["licence"]["needed"] is False
    assert client.get("/api/auth/me").get_json()["licence"]["needed"] is False
    mine = [x for x in rows(app) if x.organization_id == org_id]
    assert len(mine) == 1 and mine[0].client_ip == "127.0.0.1"
    with app.app_context():
        boss = db.session.scalar(select(User).where(User.email == "boss@shop.test"))
        assert mine[0].user_id == boss.id

    # Clicking again (a second tab) changes nothing and writes nothing more.
    assert accept(client, h).status_code == 200
    assert len([x for x in rows(app) if x.organization_id == org_id]) == 1
    assert len([a for a in audit_rows(app) if a.organization_id == org_id]) == 1


@pytest.mark.parametrize("role", ["manager", "fulfillment", "viewer"])
def test_staff_are_never_asked_and_cannot_accept(client, app, sign_in, role):
    org_id = old_shop(app)
    h = sign_in(org_id, role, email=f"{role}@shop.test")
    assert client.get("/api/auth/me").get_json()["licence"]["needed"] is False
    r = accept(client, h)
    assert r.status_code == 403
    assert [x for x in rows(app) if x.organization_id == org_id] == []


def test_the_developers_support_account_never_accepts_for_the_shop(
    client, app, sign_in, monkeypatch
):
    monkeypatch.setenv("IT_SUPPORT", "it@sotodev.test")
    org_id = old_shop(app)
    h = sign_in(org_id, "owner", email="it@sotodev.test")
    assert client.get("/api/auth/me").get_json()["licence"]["needed"] is False
    r = accept(client, h)
    assert r.status_code == 403 and "owner" in r.get_json()["error"]
    assert [x for x in rows(app) if x.organization_id == org_id] == []


@pytest.mark.parametrize("version", ["0.9", None, 1.0, ""])
def test_a_page_showing_another_version_cannot_accept(client, app, sign_in, version):
    org_id = old_shop(app)
    h = sign_in(org_id, "owner", email="boss@shop.test")
    r = client.post("/api/auth/licence", json={"version": version}, headers=h)
    assert r.status_code == 409 and "reload" in r.get_json()["error"]
    assert [x for x in rows(app) if x.organization_id == org_id] == []


def test_accepting_needs_a_session_and_the_csrf_token(client, app, sign_in):
    assert client.post("/api/auth/licence", json={"version": "1.0"}).status_code == 403
    org_id = old_shop(app)
    sign_in(org_id, "owner", email="boss@shop.test")
    r = client.post("/api/auth/licence", json={"version": licence.LICENCE_VERSION})
    assert r.status_code == 403 and "CSRF" in r.get_json()["error"]
    other = app.test_client()
    h = csrf(other)
    assert other.post("/api/auth/licence", json={"version": "1.0"}, headers=h).status_code == 401
    assert [x for x in rows(app) if x.organization_id == org_id] == []


def test_accepting_counts_toward_the_persons_write_limit(client, app, sign_in, monkeypatch):
    from app import auth as auth_mod

    h = sign_in(old_shop(app), "owner", email="boss@shop.test")
    monkeypatch.setattr(auth_mod, "WRITES_PER_MIN", 2)
    codes = [accept(client, h).status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and codes[-1] == 429


# ---------- a new version ----------


def test_a_new_version_asks_again_and_keeps_the_old_record(client, app, monkeypatch):
    r, h = signup(client)
    org_id = r.get_json()["organization"]["id"]
    monkeypatch.setattr(licence, "LICENCE_VERSION", "1.1")
    assert client.get("/api/auth/me").get_json()["licence"] == {"version": "1.1", "needed": True}
    assert accept(client, h, "1.0").status_code == 409  # the page still showing 1.0
    assert accept(client, h, "1.1").status_code == 200
    assert client.get("/api/auth/me").get_json()["licence"]["needed"] is False
    assert [(x.organization_id, x.version) for x in rows(app)] == [(org_id, "1.0"), (org_id, "1.1")]


def test_two_owners_clicking_at_once_record_one_acceptance(client, app, sign_in):
    org_id = old_shop(app)
    sign_in(org_id, "owner", email="boss@shop.test")
    with app.app_context():
        boss_id = db.session.scalar(select(User.id).where(User.email == "boss@shop.test"))
    made: list[bool] = []
    lock = threading.Lock()

    def click():
        with app.app_context():
            try:
                user = db.session.get(User, boss_id)
                result = licence.accept(user, licence.LICENCE_VERSION, "10.0.0.1")
                with lock:
                    made.append(result)
            finally:
                db.session.remove()

    threads = [threading.Thread(target=click) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(made) == [False] * 7 + [True]
    assert len([x for x in rows(app) if x.organization_id == org_id]) == 1
    assert len([a for a in audit_rows(app) if a.organization_id == org_id]) == 1


def test_a_shop_whose_record_is_gone_is_asked_again(client, app):
    """The banner reads the table, not a flag on the user: no row, ask."""
    r, _ = signup(client)
    with app.app_context():
        db.session.execute(delete(LicenceAcceptance))
        db.session.commit()
    assert client.get("/api/auth/me").get_json()["licence"]["needed"] is True

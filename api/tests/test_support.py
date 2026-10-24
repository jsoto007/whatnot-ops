"""IT_SUPPORT / IT_SUPPORT_PASSWORD: the developer's protected owner account."""

import pytest
from sqlalchemy import select
from test_auth import PW, csrf, signup

from app.db import db
from app.models import AuditLog, Role, User
from app.services import support
from app.services.accounts import AccountError

SUPPORT = "it@sotodev.test"
SUPPORT_PW = "support-pass-123"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("IT_SUPPORT", "  IT@SotoDev.test ")
    monkeypatch.setenv("IT_SUPPORT_PASSWORD", SUPPORT_PW)


def _user(app, email):
    with app.app_context():
        return db.session.scalar(select(User).where(User.email == email))


def _login(app, email, password):
    c = app.test_client()
    body = {"email": email, "password": password}
    return c, c.post("/api/auth/login", json=body, headers=csrf(c))


def test_unset_means_no_account(client, app, monkeypatch):
    monkeypatch.delenv("IT_SUPPORT", raising=False)
    monkeypatch.delenv("IT_SUPPORT_PASSWORD", raising=False)
    signup(client)
    with app.app_context():
        assert support.ensure_support_account() is None
        assert db.session.scalar(select(db.func.count()).select_from(User)) == 1


def test_only_one_variable_creates_nothing(client, app, monkeypatch):
    signup(client)
    monkeypatch.setenv("IT_SUPPORT", SUPPORT)
    monkeypatch.delenv("IT_SUPPORT_PASSWORD", raising=False)
    with app.app_context():
        assert support.ensure_support_account() is None
    assert _user(app, SUPPORT) is None


def test_weak_password_is_refused(app, monkeypatch):
    monkeypatch.setenv("IT_SUPPORT", SUPPORT)
    monkeypatch.setenv("IT_SUPPORT_PASSWORD", "short")
    with app.app_context(), pytest.raises(AccountError):
        support.ensure_support_account()


def test_no_shop_yet_then_created_by_signup(client, app, configured):
    with app.app_context():
        assert support.ensure_support_account() is None  # nothing to join yet
    signup(client, email="owner@shop.test")
    u = _user(app, SUPPORT)
    assert u is not None and u.role is Role.owner and not u.disabled
    assert u.name == support.SUPPORT_NAME
    c, r = _login(app, SUPPORT, SUPPORT_PW)
    assert r.status_code == 200 and r.get_json()["user"]["role"] == "owner"
    users = {x["email"]: x for x in c.get("/api/team").get_json()["users"]}
    assert users[SUPPORT]["isAdmin"] is True
    with app.app_context():
        actions = [a for (a,) in db.session.execute(select(AuditLog.action)).all()]
    assert "support.created" in actions


def test_environment_wins_over_the_database(client, app, configured):
    signup(client)
    with app.app_context():
        u = db.session.scalar(select(User).where(User.email == SUPPORT))
        u.role = Role.viewer
        u.disabled = True
        u.password_hash = "x"
        db.session.commit()
        restored = support.ensure_support_account()
        assert restored.role is Role.owner and not restored.disabled
        entry = db.session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "support.restored")
            .order_by(AuditLog.id.desc())
        )
        assert set(entry.details) == {"role", "disabled", "password"}
    _, r = _login(app, SUPPORT, SUPPORT_PW)
    assert r.status_code == 200
    with app.app_context():
        assert support.ensure_support_account() is not None  # idempotent: no new audit row
        restored_rows = select(db.func.count()).select_from(AuditLog)
        restored_rows = restored_rows.where(AuditLog.action == "support.restored")
        assert db.session.scalar(restored_rows) == 1


def test_cannot_be_demoted_or_disabled_and_login_restores(client, app, configured):
    signup(client)
    h = csrf(client)
    support_id = _user(app, SUPPORT).id
    r = client.patch(f"/api/team/users/{support_id}", json={"role": "viewer"}, headers=h)
    assert r.status_code == 409 and "IT_SUPPORT" in r.get_json()["error"]
    r = client.patch(f"/api/team/users/{support_id}", json={"disabled": True}, headers=h)
    assert r.status_code == 409
    with app.app_context():
        u = db.session.get(User, support_id)
        u.role = Role.viewer
        u.disabled = True
        db.session.commit()
    _, r = _login(app, SUPPORT, SUPPORT_PW)
    assert r.status_code == 200 and r.get_json()["user"]["role"] == "owner"


def test_cli_reports_the_account(client, app, configured):
    runner = app.test_cli_runner()
    assert "no shop yet" in runner.invoke(args=["ensure-support"]).output
    signup(client)
    out = runner.invoke(args=["ensure-support"]).output
    assert f"support account {SUPPORT} is an owner of 'My Shop'" in out
    assert SUPPORT_PW not in out


def test_cli_fails_loudly_on_a_bad_password(app, monkeypatch):
    monkeypatch.setenv("IT_SUPPORT", SUPPORT)
    monkeypatch.setenv("IT_SUPPORT_PASSWORD", "short")
    result = app.test_cli_runner().invoke(args=["ensure-support"])
    assert result.exit_code != 0 and "misconfigured" in result.output


def test_the_admin_email_still_creates_the_shop_alone(client, app, configured, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "boss@shop.test")
    r, _ = signup(client, email=SUPPORT)
    assert r.status_code == 403  # support is not the admin
    r, _ = signup(client, email="boss@shop.test", password=PW)
    assert r.status_code == 201
    assert _user(app, SUPPORT).role is Role.owner

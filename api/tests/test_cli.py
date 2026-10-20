from sqlalchemy import select

from app.db import db
from app.models import Device, Organization
from app.services.devices import device_for_key


def test_devices_create_prints_key_once_and_stores_only_the_hash(app):
    runner = app.test_cli_runner()
    r = runner.invoke(args=["devices", "create", "--org", "My Shop", "--name", "Front laptop"])
    assert r.exit_code == 0, r.output
    key = r.output.strip().splitlines()[-1]
    assert key.startswith("wn_")
    with app.app_context():
        d = db.session.scalar(select(Device))
        assert d.name == "Front laptop" and d.key_hash != key and key not in d.key_hash
        assert d.key_prefix == key[:10]
        assert db.session.scalar(select(Organization)).slug == "my-shop"
        assert device_for_key(key).id == d.id
        assert device_for_key("wn_other") is None


def test_e2e_seed_is_refused_in_production(app, monkeypatch):
    monkeypatch.setenv("E2E_DEVICE_KEY", "wn_e2e")
    app.config["APP_ENV"] = "production"
    try:
        r = app.test_cli_runner().invoke(args=["e2e-seed"])
        assert r.exit_code != 0 and "refused" in r.output
    finally:
        app.config["APP_ENV"] = "test"


def test_e2e_seed_creates_device_with_given_key_idempotently(app, monkeypatch):
    monkeypatch.setenv("E2E_DEVICE_KEY", "wn_e2e-fixed-key")
    runner = app.test_cli_runner()
    assert runner.invoke(args=["e2e-seed"]).exit_code == 0
    assert runner.invoke(args=["e2e-seed"]).exit_code == 0
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(Device)) == 1
        assert device_for_key("wn_e2e-fixed-key") is not None

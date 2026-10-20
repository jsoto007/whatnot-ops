from sqlalchemy import select

from app.cli.seed_demo import DEMO_OWNER, DEMO_PASSWORD
from app.db import db
from app.models import Order, OrderStatus, ReviewItem, Sale, Show


def test_seed_demo_builds_a_coherent_dataset(app, client):
    r = app.test_cli_runner().invoke(args=["seed-demo"])
    assert r.exit_code == 0, r.output
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(Show)) == 3
        assert db.session.scalar(select(db.func.count()).select_from(Sale)) > 15
        statuses = {s for (s,) in db.session.execute(select(Order.status).distinct())}
        assert {
            OrderStatus.paid,
            OrderStatus.picking,
            OrderStatus.packed,
            OrderStatus.shipped,
        } <= statuses
        assert db.session.scalar(select(db.func.count()).select_from(ReviewItem)) >= 3
    # the seeded owner can sign in
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    assert (
        client.post(
            "/api/auth/login", json={"email": DEMO_OWNER, "password": DEMO_PASSWORD}, headers=h
        ).status_code
        == 200
    )
    assert client.get("/api/dashboard").get_json()["totals"]["shows"] == 3
    # running it twice is refused rather than duplicating
    assert app.test_cli_runner().invoke(args=["seed-demo"]).exit_code != 0


def test_seed_demo_is_refused_in_production(app):
    app.config["APP_ENV"] = "production"
    try:
        r = app.test_cli_runner().invoke(args=["seed-demo"])
        assert r.exit_code != 0 and "refused" in r.output
    finally:
        app.config["APP_ENV"] = "test"

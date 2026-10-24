from app.models import Role
from app.services.permissions import PERMISSIONS, allowed, invitable_roles, permissions_for

PW = "correct-horse-battery"


def test_owner_has_every_permission():
    assert set(permissions_for(Role.owner)) == set(PERMISSIONS)


def test_viewer_is_read_only():
    for p in permissions_for(Role.viewer):
        assert p.endswith(".view") or p == "reports.view", p


def test_fulfillment_can_fulfill_but_not_manage():
    assert allowed(Role.fulfillment, "orders.fulfill")
    assert not allowed(Role.fulfillment, "inventory.write")
    assert not allowed(Role.fulfillment, "team.view")


def test_invitable_roles_narrow_by_role():
    assert invitable_roles(Role.owner) == frozenset(Role)
    assert invitable_roles(Role.manager) == {Role.fulfillment, Role.viewer}
    assert invitable_roles(Role.fulfillment) == frozenset()


def test_fulfillment_lands_on_a_dashboard_it_may_read(app, client):
    """`/app` renders the overview for every role, so `GET /api/dashboard` must answer
    everyone who may see shows; the spreadsheet downloads stay behind reports.view."""
    h = {"X-CSRF-Token": client.get("/api/auth/csrf").get_json()["token"]}
    r = client.post(
        "/api/auth/signup",
        json={"organization": "Shop", "name": "O", "email": "o@x.test", "password": PW},
        headers=h,
    )
    assert r.status_code == 201, r.get_json()
    token = (
        client.post(
            "/api/team/invites", json={"email": "p@x.test", "role": "fulfillment"}, headers=h
        )
        .get_json()["acceptUrl"]
        .rsplit("/", 1)[1]
    )
    pat = app.test_client()
    hp = {"X-CSRF-Token": pat.get("/api/auth/csrf").get_json()["token"]}
    r = pat.post(
        "/api/auth/invites/accept", json={"token": token, "name": "P", "password": PW}, headers=hp
    )
    assert r.status_code == 201, r.get_json()
    assert "reports.view" not in pat.get("/api/auth/me").get_json()["permissions"]
    d = pat.get("/api/dashboard")
    assert d.status_code == 200, d.get_json()
    assert d.get_json()["shows"] == [] and d.get_json()["attention"]["toFulfil"] == 0
    assert pat.get("/api/reports/overview.xlsx").status_code == 403

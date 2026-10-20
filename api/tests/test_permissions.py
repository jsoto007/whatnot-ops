from app.models import Role
from app.services.permissions import PERMISSIONS, allowed, invitable_roles, permissions_for


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

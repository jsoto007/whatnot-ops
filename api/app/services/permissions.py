"""The one place roles map to what they may do. Routes declare a permission, never a role."""

from ..models import Role

ALL = frozenset(Role)
OWNER = frozenset({Role.owner})
MANAGERS = frozenset({Role.owner, Role.manager})

PERMISSIONS: dict[str, frozenset[Role]] = {
    "team.view": MANAGERS,
    "team.invite": MANAGERS,  # managers may only invite fulfillment/viewer (invitable_roles)
    "team.manage": OWNER,
    "devices.manage": MANAGERS,
    "audit.view": MANAGERS,
    "settings.manage": OWNER,
    "shows.view": ALL,
    "inventory.view": ALL,
    "inventory.write": MANAGERS,
    "orders.view": ALL,
    "orders.fulfill": frozenset({Role.owner, Role.manager, Role.fulfillment}),
    "review.act": MANAGERS,
    "reports.view": frozenset({Role.owner, Role.manager, Role.viewer}),
}


def allowed(role: Role, permission: str) -> bool:
    return role in PERMISSIONS[permission]


def permissions_for(role: Role) -> list[str]:
    return sorted(p for p, roles in PERMISSIONS.items() if role in roles)


def invitable_roles(role: Role) -> frozenset[Role]:
    if role is Role.owner:
        return ALL
    if role is Role.manager:
        return frozenset({Role.fulfillment, Role.viewer})
    return frozenset()

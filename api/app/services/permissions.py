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
    "whatnot.view": MANAGERS,  # connection health; nothing secret is ever shown
    "whatnot.manage": OWNER,  # connect / disconnect the shop's Whatnot seller account
    "shows.view": ALL,
    "inventory.view": ALL,
    "inventory.write": MANAGERS,
    "orders.view": ALL,
    "orders.fulfill": frozenset({Role.owner, Role.manager, Role.fulfillment}),
    "orders.reconcile": MANAGERS,  # importing payment records changes stock (commit)
    # Asking Whatnot to make labels closes each buyer's bundle and cannot be undone.
    "orders.labels": MANAGERS,
    "review.act": MANAGERS,
    # Every role sees who a buyer is (the packing table needs the flag); a viewer only reads.
    "buyers.view": ALL,
    "buyers.manage": frozenset({Role.owner, Role.manager, Role.fulfillment}),
    "reports.view": frozenset({Role.owner, Role.manager, Role.viewer}),
}


ROLE_LABELS = {
    Role.owner: "Owner",
    Role.manager: "Manager",
    Role.fulfillment: "Fulfillment",
    Role.viewer: "Viewer",
}


def role_label(role: Role | str | None) -> str:
    """A role as the shop reads it ("Owner"), for messages and the activity log."""
    try:
        return ROLE_LABELS[Role(role)]
    except ValueError:
        return "team member"


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

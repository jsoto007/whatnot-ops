from ..db import db
from ..models import AuditLog, Device, User


def record(
    org_id: int,
    action: str,
    *,
    user: User | None = None,
    device: Device | None = None,
    actor_type: str | None = None,
    actor_label: str = "",
    target_type: str | None = None,
    target_id: str | int | None = None,
    details: dict | None = None,
) -> AuditLog:
    if user is not None:
        actor_type, actor_id, actor_label = "user", user.id, user.email
    elif device is not None:
        actor_type, actor_id, actor_label = "device", device.id, device.name
    else:
        actor_type, actor_id = actor_type or "system", None
    entry = AuditLog(
        organization_id=org_id,
        actor_type=actor_type,
        actor_id=actor_id,
        actor_label=actor_label,
        action=action,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        details=details,
    )
    db.session.add(entry)
    return entry

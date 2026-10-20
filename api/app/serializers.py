from .models import AuditLog, Device, Invite, Organization, Show, User
from .services.permissions import permissions_for


def organization(o: Organization) -> dict:
    return {"id": o.id, "name": o.name, "slug": o.slug}


def user(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role.value,
        "disabled": u.disabled,
        "createdAt": u.created_at.isoformat(),
        "lastLoginAt": u.last_login_at.isoformat() if u.last_login_at else None,
    }


def me(u: User) -> dict:
    return {
        "user": user(u),
        "organization": organization(u.organization),
        "permissions": permissions_for(u.role),
    }


def invite(i: Invite) -> dict:
    return {
        "id": i.id,
        "email": i.email,
        "role": i.role.value,
        "invitedBy": i.invited_by.name if i.invited_by else None,
        "expiresAt": i.expires_at.isoformat(),
        "pending": i.pending,
    }


def device(d: Device) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "keyPrefix": d.key_prefix,
        "createdAt": d.created_at.isoformat(),
        "lastSeenAt": d.last_seen_at.isoformat() if d.last_seen_at else None,
        "revoked": d.revoked_at is not None,
    }


def audit_entry(a: AuditLog) -> dict:
    return {
        "id": a.id,
        "at": a.created_at.isoformat(),
        "actorType": a.actor_type,
        "actor": a.actor_label,
        "action": a.action,
        "targetType": a.target_type,
        "targetId": a.target_id,
        "details": a.details,
    }


def show(s: Show, message_count: int) -> dict:
    return {
        "id": s.id,
        "key": s.show_key,
        "date": s.show_date.isoformat(),
        "startedAt": s.started_at.isoformat(),
        "updatedAt": s.updated_at.isoformat(),
        "sourceUrl": s.source_url,
        "messageCount": message_count,
    }


def alias(a) -> dict:
    return {"id": a.id, "alias": a.alias}


def sku(s) -> dict:
    return {
        "id": s.id,
        "productId": s.product_id,
        "code": s.code,
        "name": s.name,
        "priceCents": s.price_cents,
        "onHand": s.on_hand,
        "reserved": s.reserved,
        "available": s.available,
        "lowStockAt": s.low_stock_at,
        "low": s.available <= s.low_stock_at,
        "active": s.active,
        "aliases": [alias(a) for a in s.aliases],
    }


def product(p) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "notes": p.notes,
        "active": p.active,
        "skus": [sku(s) for s in p.skus],
    }


def movement(m) -> dict:
    return {
        "id": m.id,
        "kind": m.kind.value,
        "quantity": m.quantity,
        "sourceType": m.source_type,
        "sourceId": m.source_id,
        "reason": m.reason,
        "actor": m.actor_label or m.actor_type,
        "onHandAfter": m.on_hand_after,
        "reservedAfter": m.reserved_after,
        "at": m.created_at.isoformat(),
    }


def import_row(r) -> dict:
    return {
        "item": r.item,
        "quantity": r.quantity,
        "skuCode": r.sku_code,
        "action": r.action,
        "before": r.before,
        "error": r.error,
    }

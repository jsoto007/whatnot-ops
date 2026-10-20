from .models import AuditLog, Device, Invite, Organization, Show, User
from .services.permissions import permissions_for


def organization(o: Organization) -> dict:
    return {"id": o.id, "name": o.name, "slug": o.slug, "hostUsername": o.host_username}


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
        "short": s.short,
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


def event(e) -> dict:
    return {
        "id": e.id,
        "messageId": e.message_id,
        "seq": e.message.seq,
        "kind": e.kind.value,
        "buyer": e.buyer_username,
        "item": e.item_text,
        "quantity": e.quantity,
        "priceCents": e.price_cents,
        "auctionType": e.auction_type,
        "rule": e.rule,
        "confidence": e.confidence,
        "rawText": e.message.raw_text,
        "capturedAt": e.message.captured_at.isoformat(),
    }


def sale(s) -> dict:
    return {
        "id": s.id,
        "showId": s.show_id,
        "showDate": s.show.show_date.isoformat(),
        "buyer": s.buyer_username,
        "sku": {
            "id": s.sku.id,
            "code": s.sku.code,
            "name": s.sku.name,
            "productName": s.sku.product.name,
        }
        if s.sku
        else None,
        "item": s.item_text,
        "quantity": s.quantity,
        "priceCents": s.price_cents,
        "auctionType": s.auction_type,
        "status": s.status.value,
        "statusReason": s.status_reason,
        "flagged": s.flagged,
        "orderId": s.order_id,
        "externalOrderId": s.external_order_id,
        "paidAt": s.paid_at.isoformat() if s.paid_at else None,
        "seq": s.event.message.seq if s.event else None,
        "rawText": s.event.message.raw_text if s.event else None,
        "createdAt": s.created_at.isoformat(),
    }


def chat_message(m) -> dict:
    return {
        "id": m.id,
        "seq": m.seq,
        "username": m.username,
        "body": m.body,
        "rawText": m.raw_text,
        "capturedAt": m.captured_at.isoformat(),
    }


def review_item(r, context) -> dict:
    return {
        "id": r.id,
        "showId": r.show_id,
        "showDate": r.show_id and context["show_date"],
        "reason": r.reason,
        "summary": r.summary,
        "source": r.source,
        "status": r.status.value,
        "proposal": r.proposal,
        "message": chat_message(r.message) if r.message else None,
        "context": [
            {"seq": m.seq, "username": m.username, "body": m.body} for m in context["lines"]
        ],
        "sale": sale(r.sale) if r.sale else None,
        "createdAt": r.created_at.isoformat(),
    }


def order(o, *, with_sales: bool = True) -> dict:
    from .services.orders import verified

    standing = [
        s for s in o.sales if s.status.value in ("detected", "reserved", "paid", "fulfilled")
    ]
    return {
        "id": o.id,
        "showId": o.show_id,
        "showDate": o.show.show_date.isoformat(),
        "buyer": o.buyer_username,
        "status": o.status.value,
        "verified": verified(o),
        "items": sum(s.quantity for s in standing),
        "totalCents": sum((s.price_cents or 0) * s.quantity for s in standing),
        "notes": o.notes,
        "tracking": o.tracking,
        "pickedAt": o.picked_at.isoformat() if o.picked_at else None,
        "packedAt": o.packed_at.isoformat() if o.packed_at else None,
        "shippedAt": o.shipped_at.isoformat() if o.shipped_at else None,
        "createdAt": o.created_at.isoformat(),
        "sales": [sale(s) for s in o.sales] if with_sales else None,
    }


def reconcile_row(r) -> dict:
    return {
        "buyer": r.buyer,
        "item": r.item,
        "quantity": r.quantity,
        "priceCents": r.price_cents,
        "status": r.status,
        "externalId": r.external_id,
        "action": r.action,
        "saleId": r.sale_id,
        "note": r.note,
    }

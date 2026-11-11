from .models import AuditLog, Device, Invite, Organization, Show, User
from .services import activity, rules
from .services.admin import is_protected
from .services.permissions import allowed, permissions_for


def organization(o: Organization) -> dict:
    return {"id": o.id, "name": o.name, "slug": o.slug, "hostUsername": o.host_username}


def user(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "name": u.name,
        "role": u.role.value,
        "disabled": u.disabled,
        "isAdmin": is_protected(u.email),
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
        "extensionVersion": d.extension_version,
        # Who made the key; None for keys from before that was recorded (or the CLI).
        "createdBy": d.created_by.name if d.created_by else None,
        # A key made before 0018 by someone already disabled or demoted was not revoked
        # by it: say so, so the page does not read as if that key were covered.
        "makerLostAccess": d.created_by is not None
        and (d.created_by.disabled or not allowed(d.created_by.role, "devices.manage")),
    }


def audit_entry(a: AuditLog, target: str = "") -> dict:
    """Only words: the raw action code and details stay in the database
    (services/activity.py)."""
    return {
        "id": a.id,
        "at": a.created_at.isoformat(),
        "who": activity.who(a.action, a.actor_type, a.actor_label),
        "summary": activity.describe(a.action, a.details, target),
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
        # The show's title on Whatnot, once the recording is matched to it; and whether a
        # laptop recorded it at all (False: the app made the show from Whatnot's own record).
        "title": s.whatnot_show.title if s.whatnot_show is not None else None,
        "recorded": s.whatnot_link != "made",
    }


def alias(a) -> dict:
    return {"id": a.id, "alias": a.alias}


def ounces(w) -> float | None:
    """A SKU's weight (Decimal, 0.01 oz) as a JSON number; null when not weighed."""
    return None if w is None else float(w)


def sku(s) -> dict:
    return {
        "id": s.id,
        "productId": s.product_id,
        "code": s.code,
        "name": s.name,
        "priceCents": s.price_cents,
        "weightOz": ounces(s.weight_oz),
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


def movement(m, names: dict | None = None) -> dict:
    """`names`: user id -> name for the people in these rows (activity.user_names)."""
    return {
        "id": m.id,
        "kind": m.kind.value,
        "quantity": m.quantity,
        "sourceType": m.source_type,
        "sourceId": m.source_id,
        "reason": m.reason,
        "actor": activity.movement_actor(m.actor_type, m.actor_id, m.actor_label, names or {}),
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


def sale(s, chat: list[dict] | None = None, *, review=None) -> dict:
    """`review` is the open review item waiting on this sale, when the caller looked it up."""
    # A sale the app learnt of from Whatnot's order has no chat line: its order time.
    won = s.event.message.captured_at if s.event else s.ordered_at
    out = {
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
        "itemTitle": rules.item_title(s.item_text or ""),
        "lot": rules.lot_number(s.item_text or ""),
        "quantity": s.quantity,
        "priceCents": s.price_cents,
        "auctionType": s.auction_type,
        "status": s.status.value,
        "statusReason": s.status_reason,
        "flagged": s.flagged,
        "giftCount": s.gift_count,
        "orderId": s.order_id,
        "externalOrderId": s.external_order_id,
        "paidAt": s.paid_at.isoformat() if s.paid_at else None,
        "seq": s.event.message.seq if s.event else None,
        "rawText": s.event.message.raw_text if s.event else None,
        "source": s.event.message.source
        if s.event
        else ("whatnot" if s.external_order_id and s.ordered_at else None),
        "wonAt": won.isoformat() if won else None,
        "review": {"id": review.id, "reason": review.reason} if review is not None else None,
        "createdAt": s.created_at.isoformat(),
    }
    if chat is not None:
        out["chat"] = chat
    return out


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


def order(o, *, with_sales: bool = True, with_gifts: bool = False, reviews=None) -> dict:
    """`with_gifts` adds the buyer's gift lines for the show (one query; detail view only).
    `reviews` maps sale id → the open review item waiting on it (routes/orders looks them up)."""
    from .services.orders import gifts_for, verified

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
        "packedBy": o.packed_by,
        "shippedAt": o.shipped_at.isoformat() if o.shipped_at else None,
        "createdAt": o.created_at.isoformat(),
        "sales": [sale(s, review=(reviews or {}).get(s.id)) for s in o.sales]
        if with_sales
        else None,
        "gifts": gifts_for(o) if with_gifts else None,
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


def whatnot_show(ws) -> dict:
    """A show as Whatnot has it. `id` is the app's row id (never Whatnot's own)."""
    start = ws.started_at or ws.scheduled_start_at
    return {
        "id": ws.id,
        "title": ws.title,
        "status": ws.status,
        "startsAt": start.isoformat() if start else None,
        "endedAt": ws.ended_at.isoformat() if ws.ended_at else None,
        "url": ws.url,
        "ordersReadAt": ws.orders_synced_at.isoformat() if ws.orders_synced_at else None,
    }

"""The shop's Whatnot listings next to its own products. Read-only towards Whatnot.

1. **Mirror**: every product variant in the shop's Whatnot inventory is kept in
   `whatnot_variants`: its title, Whatnot's SKU, price and how many Whatnot says are
   available. Read once a day (not while a show is on air: the read shares the request
   budget the orders need), on "Read from Whatnot now", and when a product webhook says
   one changed.
2. **Link**: a listing is tied to the product here it is the same thing as. By itself when
   Whatnot's SKU (or external id) equals a product code here; otherwise by a person. A
   person's "none of ours" is remembered: no code match and no title match names a
   product for it afterwards. The link is what lets an order for that listing name its
   product without anyone picking it (`resolver`), and what Inventory shows as "on
   Whatnot", with a note when Whatnot offers a different number than the shop can sell.

**The app never changes Whatnot's stock.** A first version set Whatnot's count from the
app; its review showed that cannot be right without knowing more than the app knows:
Whatnot counts its own sales down, and the app only learns of a sale when its order is
read and applied (never, in "Compare only", or for a sale made between shows), so setting
a number would put sold units back. It was removed. People fix a difference on Whatnot.

Nothing here changes a sale, an order or the app's own stock."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from ...db import db
from ...models import Sku, User, WhatnotOrderItem, WhatnotShow, WhatnotVariant
from .. import audit, jobs, text
from . import connection, webhooks
from .client import Forbidden, NotFound, WhatnotClient, WhatnotError, client_for, price_cents

log = logging.getLogger(__name__)

CATALOG_EVERY = timedelta(hours=24)
MAX_PRODUCTS = 2000  # a catalog larger than this is read up to here, and says so
COMMIT_EVERY = 25  # products per transaction: a long read holds no lock for long
LIVE = ("live", "stopped")
LIVE_WITHIN = timedelta(hours=24)  # a "live" show older than this was never closed here
RESOURCE = "catalog"


class CatalogError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _str(value, limit: int) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = (text.clean(value) or "").strip()
    return value[:limit] or None


def _options(variant: dict) -> str:
    """ "Red / L" from the variant's selected options."""
    picked = variant.get("selected_options")
    values = []
    for o in picked if isinstance(picked, list) else []:
        value = _str(o.get("value") if isinstance(o, dict) else None, 60)
        if value:
            values.append(value)
    return " / ".join(values)[:240]


def _quantity(variant: dict) -> int | None:
    inventory = variant.get("inventory")
    n = inventory.get("available_quantity") if isinstance(inventory, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 10_000_000 else None


# ---------- the mirror ----------


def store_product(org_id: int, product: dict, variants: list[dict], *, now: datetime) -> int:
    """Keep one product's variants as Whatnot has them. Returns how many were stored. A
    variant of the product that is not in `variants` any more is marked gone."""
    product_id = _str(product.get("id"), 20)
    if product_id is None:
        return 0
    title = _str(product.get("title"), 300) or ""
    url = text.whatnot_url(product.get("url"))
    known = {
        v.whatnot_id: v
        for v in db.session.scalars(
            select(WhatnotVariant).where(
                WhatnotVariant.organization_id == org_id, WhatnotVariant.product_id == product_id
            )
        )
    }
    seen = set()
    for data in variants:
        variant_id = _str(data.get("id"), 40) if isinstance(data, dict) else None
        if variant_id is None:
            continue
        seen.add(variant_id)
        row = known.get(variant_id)
        if row is None:
            row = WhatnotVariant(
                organization_id=org_id, whatnot_id=variant_id, product_id=product_id
            )
            db.session.add(row)
        price = data.get("price") if isinstance(data.get("price"), dict) else {}
        row.title, row.url, row.options = title, url, _options(data)
        row.sku_code = _str(data.get("sku"), 80)
        row.external_id = _str(data.get("external_id"), 120)
        row.price_cents = price_cents(price) if price else None
        row.currency = _str(price.get("currency"), 3)
        row.available = _quantity(data)
        row.gone_at, row.synced_at = None, now
    for variant_id, row in known.items():
        if variant_id not in seen and row.gone_at is None:
            row.gone_at = now
    db.session.flush()
    return len(seen)


def _mark_gone(org_id: int, product_id: str, now: datetime) -> None:
    for row in db.session.scalars(
        select(WhatnotVariant).where(
            WhatnotVariant.organization_id == org_id,
            WhatnotVariant.product_id == str(product_id)[:20],
            WhatnotVariant.gone_at.is_(None),
        )
    ):
        row.gone_at = now


def sync_product(org_id: int, client: WhatnotClient, product_id: str) -> int:
    """Read one product again (a webhook said it changed, or was deleted)."""
    now = datetime.now(UTC)
    try:
        product = client.product(product_id)
        variants = client.variants(str(product["id"])) if product else []
    except NotFound:
        product = None
    if not product:
        _mark_gone(org_id, product_id, now)
        return 0
    n = store_product(org_id, product, variants, now=now)
    auto_link(org_id)
    return n


def sync_catalog(org_id: int, client: WhatnotClient) -> dict:
    """Read the whole catalog, a few products per transaction (it commits as it goes: a
    long read must not sit on rows a webhook wants). A listing the read no longer finds
    is marked gone, but only when the read went through every product: a read that was
    cut off, or that failed part-way, proves nothing about the rest."""
    began = datetime.now(UTC)
    products = variants = 0
    complete = True
    for i, product in enumerate(client.products()):
        if i >= MAX_PRODUCTS:
            log.warning("whatnot catalog read for shop %s stopped at its cap", org_id)
            complete = False
            break
        product_id = _str(product.get("id") if isinstance(product, dict) else None, 20)
        if product_id is None:
            continue
        try:
            found = client.variants(product_id)
        except NotFound:
            # Deleted on Whatnot between the list and this call: it is simply gone.
            _mark_gone(org_id, product_id, began)
            continue
        variants += store_product(org_id, product, found, now=began)
        products += 1
        if products % COMMIT_EVERY == 0:
            db.session.commit()
    if complete:
        stale = db.session.scalars(
            select(WhatnotVariant).where(
                WhatnotVariant.organization_id == org_id,
                WhatnotVariant.gone_at.is_(None),
                WhatnotVariant.synced_at < began,
            )
        ).all()
        for row in stale:
            row.gone_at = began
    linked = auto_link(org_id)
    db.session.commit()
    return {"products": products, "variants": variants, "linked": linked, "complete": complete}


# ---------- the link ----------


def auto_link(org_id: int) -> int:
    """Tie each listing nobody has decided about to the product whose code equals
    Whatnot's SKU (or external id), ignoring case. Exactly equal, or not at all. Each
    link is a conditional update: a person's decision made a moment ago is never
    overwritten by a code match."""
    undecided = db.session.execute(
        select(WhatnotVariant.id, WhatnotVariant.sku_code, WhatnotVariant.external_id).where(
            WhatnotVariant.organization_id == org_id,
            WhatnotVariant.sku_id.is_(None),
            WhatnotVariant.link.is_(None),
            WhatnotVariant.gone_at.is_(None),
        )
    ).all()
    if not undecided:
        return 0
    codes = dict(
        db.session.execute(
            select(func.lower(Sku.code), Sku.id).where(
                Sku.organization_id == org_id, Sku.active.is_(True)
            )
        ).all()
    )
    n = 0
    for variant_id, sku_code, external_id in undecided:
        for candidate in (sku_code, external_id):
            sku_id = codes.get(candidate.strip().lower()) if candidate else None
            if sku_id is None:
                continue
            n += db.session.execute(
                update(WhatnotVariant)
                .where(
                    WhatnotVariant.id == variant_id,
                    WhatnotVariant.link.is_(None),
                    WhatnotVariant.sku_id.is_(None),
                )
                .values(sku_id=sku_id, link="code")
            ).rowcount
            break
    db.session.flush()
    db.session.expire_all()  # rows changed by the updates above are read fresh next time
    return n


def link(org_id: int, variant_id: int, sku_id: int | None, *, user: User) -> WhatnotVariant:
    """A person says which product a listing is (`sku_id`), or that it is none of ours
    (None). Audited. The caller commits."""
    row = db.session.scalar(
        select(WhatnotVariant)
        .where(WhatnotVariant.organization_id == org_id, WhatnotVariant.id == variant_id)
        .with_for_update()
    )
    if row is None:
        raise CatalogError("That listing is not in the shop's Whatnot catalog.", 404)
    sku = None
    if sku_id is not None:
        sku = db.session.get(Sku, sku_id)
        if sku is None or sku.organization_id != org_id:
            raise CatalogError("That product is not in this shop.", 404)
    before, was = row.sku_id, row.link
    row.sku_id = sku.id if sku is not None else None
    row.link = "person" if sku is not None else "off"
    row.linked_by_id = user.id
    if (before, was) != (row.sku_id, row.link):  # a "none of ours" is a decision too
        audit.record(
            org_id,
            "whatnot.listing_linked",
            user=user,
            target_type="sku",
            target_id=row.sku_id or before,
            details={
                "listing": row.title,
                "product": f"{sku.product.name} {sku.name}".strip() if sku is not None else None,
            },
        )
    db.session.flush()
    return row


def resolver(org_id: int):
    """A function that says which product an order item is, when its listing says so by
    itself: the listing is linked to a product here, or its SKU is exactly a product code
    here. No product otherwise (the winner then names it in chat, as for a "choose any"
    lot). Two queries for the whole run, not one per item. A listing since deleted
    on Whatnot still says what its past orders were."""
    listed = {
        whatnot_id: (sku_id, how)
        for whatnot_id, sku_id, how in db.session.execute(
            select(WhatnotVariant.whatnot_id, WhatnotVariant.sku_id, WhatnotVariant.link).where(
                WhatnotVariant.organization_id == org_id
            )
        )
    }
    codes = dict(
        db.session.execute(
            select(func.lower(Sku.code), Sku.id).where(
                Sku.organization_id == org_id, Sku.active.is_(True)
            )
        ).all()
    )

    def sku_for(item: WhatnotOrderItem) -> tuple[int | None, bool]:
        """(the product, whether a person said the listing is none of ours)."""
        if item.variant_id and item.variant_id in listed:
            sku_id, how = listed[item.variant_id]
            # A person's "none of ours" stands: no code match, and no title match either.
            return (None, True) if how == "off" else (sku_id, False)
        code = (item.sku_code or "").strip().lower()
        return (codes.get(code) if code else None), False

    return sku_for


# ---------- what the pages show ----------


def _can_sell(sku: Sku) -> int:
    """What the shop can still sell of a product: never less than nothing."""
    return max(sku.available, 0)


def for_skus(org_id: int) -> dict[int, dict]:
    """product (SKU) id → what Whatnot offers for it: how many listings, how many
    available across them, and whether that differs from what the shop can sell. Empty
    for a shop that is not connected (the counts would only be getting older)."""
    if connection.active(org_id) is None:
        return {}
    rows = db.session.execute(
        select(
            WhatnotVariant.sku_id,
            func.count(),
            func.coalesce(func.sum(WhatnotVariant.available), 0),
            func.count(WhatnotVariant.available),
        )
        .where(
            WhatnotVariant.organization_id == org_id,
            WhatnotVariant.gone_at.is_(None),
            WhatnotVariant.sku_id.is_not(None),
        )
        .group_by(WhatnotVariant.sku_id)
    ).all()
    if not rows:
        return {}
    can_sell = {
        sku.id: _can_sell(sku)
        for sku in db.session.scalars(
            select(Sku).where(Sku.id.in_([r[0] for r in rows]), Sku.organization_id == org_id)
        )
    }
    out: dict[int, dict] = {}
    for sku_id, listings, on_whatnot, counted in rows:
        known = counted == listings  # Whatnot gave a count for every listing
        out[sku_id] = {
            "listings": int(listings),
            "onWhatnot": int(on_whatnot) if known else None,
            "differs": bool(known and int(on_whatnot) != can_sell.get(sku_id, 0)),
        }
    return out


def listings(org_id: int) -> dict:
    """The catalog page: every live listing, the product it is linked to, and the two
    counts side by side. "Differs" is said per product, the same way Inventory says it:
    everything Whatnot offers for the product against what the shop can sell."""
    conn = connection.active(org_id)
    rows = (
        db.session.scalars(
            select(WhatnotVariant)
            .where(WhatnotVariant.organization_id == org_id, WhatnotVariant.gone_at.is_(None))
            .order_by(func.lower(WhatnotVariant.title), WhatnotVariant.options, WhatnotVariant.id)
        ).all()
        if conn is not None
        else []
    )
    skus = {
        s.id: s
        for s in db.session.scalars(
            select(Sku).where(Sku.id.in_({r.sku_id for r in rows if r.sku_id} or {0}))
        )
    }
    per_product = for_skus(org_id) if rows else {}
    state = connection.sync_state(org_id, RESOURCE) if conn is not None else None
    out = []
    for r in rows:
        sku = skus.get(r.sku_id) if r.sku_id else None
        product = per_product.get(sku.id) if sku is not None else None
        out.append(
            {
                "id": r.id,
                "title": r.title,
                "options": r.options,
                "skuCode": r.sku_code,
                "priceCents": r.price_cents,
                "onWhatnot": r.available,
                "url": r.url,
                "link": r.link,
                "sku": {
                    "id": sku.id,
                    "code": sku.code,
                    "name": f"{sku.product.name} {sku.name}".strip(),
                    "canSell": _can_sell(sku),
                }
                if sku is not None
                else None,
                # How many listings the product has, and what they offer together.
                "listings": product["listings"] if product else 0,
                "offered": product["onWhatnot"] if product else None,
                "differs": bool(product and product["differs"]),
            }
        )
    return {
        "connected": conn is not None,
        "readAt": state.last_ok_at.isoformat() if state is not None and state.last_ok_at else None,
        "notAllowed": bool(state is not None and (state.last_error or "").startswith("Forbidden")),
        "listings": out,
        "counts": {
            "all": len(out),
            "linked": sum(1 for r in out if r["sku"] is not None),
            "undecided": sum(1 for r in out if r["sku"] is None and r["link"] != "off"),
            "differs": sum(1 for r in out if r["differs"]),
        },
    }


# ---------- jobs, the timer, webhooks ----------


def _run(org_id: int, fn):
    """Like sync._run, for a reading the shop can live without: a token that may not read
    inventory is noted for the catalog page and does not flag the connection."""
    state = connection.sync_state(org_id, RESOURCE)
    state.last_started_at = datetime.now(UTC)
    db.session.commit()
    try:
        counts = fn()
    except WhatnotError as e:
        db.session.rollback()
        if not isinstance(e, Forbidden):
            connection.note_failure(org_id, e)
        state = connection.sync_state(org_id, RESOURCE)
        state.last_error = f"{type(e).__name__} {e.problem}".strip()[:240]
        db.session.commit()
        if isinstance(e, Forbidden):
            return None
        raise
    state = connection.sync_state(org_id, RESOURCE)
    state.last_ok_at, state.last_error = datetime.now(UTC), None
    if isinstance(counts, dict):
        state.counts = counts
    db.session.commit()
    return counts


@jobs.handler("whatnot_sync_catalog")
def sync_catalog_job(payload: dict) -> None:
    org_id = payload["org_id"]
    if connection.active(org_id) is None:
        return
    client = client_for(org_id)
    _run(org_id, lambda: sync_catalog(org_id, client))


def schedule(org_id: int) -> None:
    jobs.enqueue(
        "whatnot_sync_catalog",
        {"org_id": org_id},
        org_id=org_id,
        dedupe_key=f"whatnot_sync_catalog:{org_id}",
    )


def _show_live(org_id: int) -> bool:
    """A show of the shop is on air now. A show still marked live a day after it began
    was never closed here (its ending was missed) and does not count."""
    recent = datetime.now(UTC) - LIVE_WITHIN
    return bool(
        db.session.scalar(
            select(func.count())
            .select_from(WhatnotShow)
            .where(
                WhatnotShow.organization_id == org_id,
                WhatnotShow.status.in_(LIVE),
                func.coalesce(WhatnotShow.started_at, WhatnotShow.scheduled_start_at) >= recent,
            )
        )
    )


def due(org_id: int) -> bool:
    """Time for the daily read, and no show on air: the read makes one call per product
    out of the same request budget the show's orders need."""
    last = connection.sync_state(org_id, RESOURCE).last_started_at
    if last is not None and datetime.now(UTC) - last < CATALOG_EVERY:
        return False
    return not _show_live(org_id)


@webhooks.on("products.created", "products.updated", "products.deleted")
def _on_product(event) -> None:
    product_id = (event.ref or {}).get("product_id")
    if not product_id:
        return
    org_id = event.organization_id
    try:
        sync_product(org_id, client_for(org_id), str(product_id))
    except Forbidden:
        return  # the token may not read inventory: the catalog page says so

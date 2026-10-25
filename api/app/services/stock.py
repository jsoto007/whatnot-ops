"""The Inventory page's jobs: receive a delivery, record a shelf count, explain the ledger.

Every change still goes through `inventory.move()` (restock / adjust under the SKU
row lock). A delivery or count carries a client id, so a double-submitted form or
a retried request moves nothing twice: receipts are keyed `receive:<id>:<line>`,
counts `count:<id>:<sku>`, and the ledger's (sku, kind, source) uniqueness does
the rest.
"""

import re
import uuid

from sqlalchemy import func, select

from ..db import db
from ..models import InventoryMovement, MovementKind, Product, Sale, Show, Sku
from . import catalog, inventory
from .dashboard import STANDING  # same definition of "sold" as the dashboard
from .inventory import Actor, InventoryError

MAX_QTY = 100_000
# No "_" or "%": the id is used as a LIKE prefix (`<id>:%`) when checking for replays.
_ID = re.compile(r"^[A-Za-z0-9-]{6,40}$")


def _client_id(value) -> str:
    """The form's idempotency key; generated when the caller has none."""
    if value is None or value == "":
        return uuid.uuid4().hex
    if not isinstance(value, str) or not _ID.match(value):
        raise InventoryError("bad request id", 400)
    return value


def _qty(value, *, minimum: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            raise InventoryError(f"{label} must be a whole number", 400) from None
    if value < minimum or value > MAX_QTY:
        raise InventoryError(f"{label} must be between {minimum} and {MAX_QTY}", 400)
    return value


def _sku(org_id: int, value, label: str) -> Sku:
    try:
        sku_id = int(value)
    except (TypeError, ValueError):
        raise InventoryError(f"{label}: pick an item", 400) from None
    return catalog.get_sku(org_id, sku_id)


# ---------- receive ----------


def receive(org_id: int, lines: list, *, note: str | None, receipt_id, actor: Actor) -> dict:
    """Add a delivery to the shelf. A line is {skuId, quantity} for a known item or
    {new: {name, code?, priceCents?, lowStockAt?}, quantity} to create the item too."""
    receipt_id = _client_id(receipt_id)
    note = (note or "").strip()[:200] or None
    if not isinstance(lines, list) or not lines:
        raise InventoryError("add at least one item to receive", 400)
    if len(lines) > 200:
        raise InventoryError("a delivery can have at most 200 lines", 400)

    done = db.session.scalar(
        select(func.count())
        .select_from(InventoryMovement)
        .where(
            InventoryMovement.organization_id == org_id,
            InventoryMovement.source_type == "receive",
            InventoryMovement.source_id.like(f"{receipt_id}:%"),
        )
    )
    if done:
        return {"receiptId": receipt_id, "alreadyApplied": True, "lines": [], "units": 0}

    # Validate everything before touching stock: a delivery is all-or-nothing.
    plan: list[tuple[Sku | None, dict | None, int]] = []
    seen: set[int] = set()
    for i, raw in enumerate(lines, 1):
        if not isinstance(raw, dict):
            raise InventoryError(f"line {i} is not an item", 400)
        qty = _qty(raw.get("quantity"), minimum=1, label=f"line {i} quantity")
        new = raw.get("new")
        if new:
            if not isinstance(new, dict) or not (new.get("name") or "").strip():
                raise InventoryError(f"line {i}: a new item needs a name", 400)
            plan.append((None, new, qty))
            continue
        sku = _sku(org_id, raw.get("skuId"), f"line {i}")
        if sku.id in seen:
            raise InventoryError(f"{sku.code} is listed twice; put it on one line", 400)
        seen.add(sku.id)
        plan.append((sku, None, qty))

    out = []
    for i, (sku, new, qty) in enumerate(plan, 1):
        created = False
        if new is not None:
            price = new.get("priceCents")
            p = catalog.create_product(
                org_id,
                new["name"],
                skus=[{"code": (new.get("code") or "").strip() or None, "priceCents": price}],
            )
            sku = p.skus[0]
            if new.get("lowStockAt") not in (None, ""):
                sku.low_stock_at = _qty(new["lowStockAt"], minimum=0, label="low-stock alert")
            created = True
        inventory.move(
            sku.id,
            MovementKind.restock,
            qty,
            source_type="receive",
            source_id=f"{receipt_id}:{i}",
            reason=note,
            actor=actor,
        )
        out.append(
            {
                "skuId": sku.id,
                "code": sku.code,
                "product": sku.product.name,
                "quantity": qty,
                "onHand": sku.on_hand,
                "created": created,
            }
        )
    return {
        "receiptId": receipt_id,
        "alreadyApplied": False,
        "lines": out,
        "units": sum(x["quantity"] for x in out),
    }


# ---------- count ----------


def count(org_id: int, lines: list, *, note: str | None, count_id, actor: Actor) -> dict:
    """Set the shelf to what was counted. Unchanged items write nothing."""
    count_id = _client_id(count_id)
    reason = (note or "").strip()[:200] or "stock count"
    if not isinstance(lines, list) or not lines:
        raise InventoryError("enter at least one count", 400)
    plan: list[tuple[Sku, int]] = []
    seen: set[int] = set()
    for i, raw in enumerate(lines, 1):
        if not isinstance(raw, dict):
            raise InventoryError(f"line {i} is not a count", 400)
        sku = _sku(org_id, raw.get("skuId"), f"line {i}")
        if sku.id in seen:
            raise InventoryError(f"{sku.code} is counted twice", 400)
        seen.add(sku.id)
        plan.append((sku, _qty(raw.get("counted"), minimum=0, label=f"{sku.code} count")))

    out = []
    for sku, counted in plan:
        before = sku.on_hand
        m = inventory.set_on_hand(
            sku.id,
            counted,
            source_type="count",
            source_id=f"{count_id}:{sku.id}",
            actor=actor,
            reason=reason,
        )
        out.append(
            {
                "skuId": sku.id,
                "code": sku.code,
                "before": before if m is not None else counted,
                "after": sku.on_hand,
                "delta": m.quantity if m is not None else 0,
            }
        )
    changed = [x for x in out if x["delta"]]
    return {
        "countId": count_id,
        "lines": out,
        "changed": len(changed),
        "unchanged": len(out) - len(changed),
        "net": sum(x["delta"] for x in changed),
    }


# ---------- reading ----------


def levels(org_id: int) -> list[dict]:
    """Every SKU with what the Inventory table needs to decide what to do next."""
    from .. import serializers

    rows = db.session.execute(
        select(Sku, Product.name)
        .join(Product, Product.id == Sku.product_id)
        .where(Sku.organization_id == org_id)
        .order_by(Product.name, Sku.code)
    ).all()
    sold = dict(
        db.session.execute(
            select(Sale.sku_id, func.sum(Sale.quantity))
            .where(Sale.organization_id == org_id, Sale.status.in_(STANDING))
            .group_by(Sale.sku_id)
        ).all()
    )
    last = {
        (sku_id, src): at
        for sku_id, src, at in db.session.execute(
            select(
                InventoryMovement.sku_id,
                InventoryMovement.source_type,
                func.max(InventoryMovement.created_at),
            )
            .where(
                InventoryMovement.organization_id == org_id,
                InventoryMovement.source_type.in_(("receive", "count", "import", "manual")),
            )
            .group_by(InventoryMovement.sku_id, InventoryMovement.source_type)
        ).all()
    }

    def latest(sku_id: int, *sources: str) -> str | None:
        found = [last[(sku_id, s)] for s in sources if (sku_id, s) in last]
        return max(found).isoformat() if found else None

    return [
        {
            **serializers.sku(s),
            "productName": pname,
            "sold": int(sold.get(s.id, 0)),
            "lastReceivedAt": latest(s.id, "receive"),
            "lastCountedAt": latest(s.id, "count", "import"),
        }
        for s, pname in rows
    ]


RUNNING_LOW = 3  # the scraper panel calls out 3, 2 and 1 left, then sold out


def running_low(org_id: int, *, at: int = RUNNING_LOW, limit: int = 100) -> list[dict]:
    """Active SKUs with `at` or fewer available, for the scraper's live alerts.

    Most-left first, so a pile of long-oversold SKUs cannot push the ones that
    just reached 3, 2 or 1 off the end of the list."""
    rows = db.session.execute(
        select(Sku, Product.name)
        .join(Product, Product.id == Sku.product_id)
        .where(
            Sku.organization_id == org_id,
            Sku.active.is_(True),
            Sku.on_hand - Sku.reserved <= at,
        )
        .order_by((Sku.on_hand - Sku.reserved).desc(), Sku.code)
        .limit(limit)
    ).all()
    return [
        {
            "skuId": s.id,
            "code": s.code,
            "name": f"{pname} · {s.name}" if s.name and s.name != pname else pname,
            "available": s.available,
        }
        for s, pname in rows
    ]


def activity(org_id: int, *, sku_id: int | None = None, limit: int = 50) -> list[dict]:
    """Ledger rows, newest first, with what a person needs to read them: which item,
    and for sale movements which buyer in which show."""
    from .. import serializers

    q = (
        select(InventoryMovement, Sku.code, Product.name)
        .join(Sku, Sku.id == InventoryMovement.sku_id)
        .join(Product, Product.id == Sku.product_id)
        .where(InventoryMovement.organization_id == org_id)
        .order_by(InventoryMovement.id.desc())
        .limit(max(1, min(limit, 200)))
    )
    if sku_id is not None:
        q = q.where(InventoryMovement.sku_id == sku_id)
    rows = db.session.execute(q).all()
    sale_ids = {
        int(m.source_id) for m, _, _ in rows if m.source_type == "sale" and m.source_id.isdigit()
    }
    sales = {
        sid: (buyer, show_id, show_date)
        for sid, buyer, show_id, show_date in db.session.execute(
            select(Sale.id, Sale.buyer_username, Sale.show_id, Show.show_date)
            .join(Show, Show.id == Sale.show_id)
            .where(Sale.id.in_(sale_ids or {0}))
        ).all()
    }
    out = []
    for m, code, pname in rows:
        d = {**serializers.movement(m), "skuId": m.sku_id, "skuCode": code, "productName": pname}
        is_sale = m.source_type == "sale" and m.source_id.isdigit()
        sale = sales.get(int(m.source_id)) if is_sale else None
        d["sale"] = (
            {"buyer": sale[0], "showId": sale[1], "showDate": sale[2].isoformat()} if sale else None
        )
        out.append(d)
    return out

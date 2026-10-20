"""Products, SKUs, aliases and the spreadsheet import."""

import csv
import io
import re
from dataclasses import dataclass, field

from sqlalchemy import select

from ..db import db
from ..models import Product, ProductAlias, Sku
from . import inventory
from .inventory import Actor


class CatalogError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def norm_alias(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def code_from_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:48] or "SKU"


def get_product(org_id: int, product_id: int) -> Product:
    p = db.session.get(Product, product_id)
    if p is None or p.organization_id != org_id:
        raise CatalogError("product not found", 404)
    return p


def get_sku(org_id: int, sku_id: int) -> Sku:
    s = db.session.get(Sku, sku_id)
    if s is None or s.organization_id != org_id:
        raise CatalogError("SKU not found", 404)
    return s


def create_product(
    org_id: int, name: str, *, notes: str | None = None, skus: list[dict] | None = None
) -> Product:
    name = (name or "").strip()
    if not name:
        raise CatalogError("product name is required")
    if db.session.scalar(
        select(Product.id).where(Product.organization_id == org_id, Product.name == name)
    ):
        raise CatalogError("a product with that name already exists", 409)
    p = Product(organization_id=org_id, name=name, notes=notes)
    db.session.add(p)
    db.session.flush()
    for s in skus or [{"code": code_from_name(name), "name": ""}]:
        add_sku(
            org_id,
            p,
            s.get("code") or code_from_name(name),
            s.get("name") or "",
            s.get("priceCents"),
        )
    return p


def add_sku(
    org_id: int, product: Product, code: str, name: str = "", price_cents: int | None = None
) -> Sku:
    code = (code or "").strip().upper()
    if not code:
        raise CatalogError("SKU code is required")
    if price_cents is not None and (not isinstance(price_cents, int) or price_cents < 0):
        raise CatalogError("price must be a whole number of cents")
    if db.session.scalar(select(Sku.id).where(Sku.organization_id == org_id, Sku.code == code)):
        raise CatalogError(f"SKU code {code} already exists", 409)
    sku = Sku(
        organization_id=org_id,
        product=product,
        code=code,
        name=(name or "").strip(),
        price_cents=price_cents,
    )
    db.session.add(sku)
    db.session.flush()
    add_alias(org_id, sku, product.name if not name else f"{product.name} {name}", quiet=True)
    return sku


def update_sku(org_id: int, sku_id: int, **fields) -> Sku:
    sku = get_sku(org_id, sku_id)
    if "code" in fields and fields["code"]:
        code = fields["code"].strip().upper()
        if code != sku.code and db.session.scalar(
            select(Sku.id).where(Sku.organization_id == org_id, Sku.code == code)
        ):
            raise CatalogError(f"SKU code {code} already exists", 409)
        sku.code = code
    if "name" in fields and fields["name"] is not None:
        sku.name = fields["name"].strip()
    if "priceCents" in fields:
        pc = fields["priceCents"]
        if pc is not None and (not isinstance(pc, int) or pc < 0):
            raise CatalogError("price must be a whole number of cents")
        sku.price_cents = pc
    if "lowStockAt" in fields and fields["lowStockAt"] is not None:
        sku.low_stock_at = max(0, int(fields["lowStockAt"]))
    if "active" in fields and fields["active"] is not None:
        sku.active = bool(fields["active"])
    return sku


def add_alias(org_id: int, sku: Sku, alias: str, *, quiet: bool = False) -> ProductAlias | None:
    alias = norm_alias(alias)
    if not alias:
        if quiet:
            return None
        raise CatalogError("alias is required")
    existing = db.session.scalar(
        select(ProductAlias).where(
            ProductAlias.organization_id == org_id, ProductAlias.alias == alias
        )
    )
    if existing is not None:
        if existing.sku_id == sku.id or quiet:
            return existing
        raise CatalogError(f'"{alias}" already points to {existing.sku.code}', 409)
    a = ProductAlias(organization_id=org_id, sku=sku, alias=alias)
    db.session.add(a)
    db.session.flush()
    return a


def remove_alias(org_id: int, alias_id: int) -> None:
    a = db.session.get(ProductAlias, alias_id)
    if a is None or a.organization_id != org_id:
        raise CatalogError("alias not found", 404)
    db.session.delete(a)


def resolve_alias(org_id: int, text: str) -> Sku | None:
    """Exact alias match, then SKU code. (Fuzzier matching is the parser's job in Phase 5.)"""
    key = norm_alias(text)
    if not key:
        return None
    a = db.session.scalar(
        select(ProductAlias).where(
            ProductAlias.organization_id == org_id, ProductAlias.alias == key
        )
    )
    if a is not None:
        return a.sku
    return db.session.scalar(
        select(Sku).where(Sku.organization_id == org_id, Sku.code == key.upper())
    )


# ---------- spreadsheet import ----------

ITEM_HEADERS = ("item", "product", "name", "sku")
QTY_HEADERS = (
    "current stock",
    "start of show inventory",
    "on hand",
    "on_hand",
    "qty",
    "quantity",
    "stock",
)


@dataclass
class ImportRow:
    item: str
    quantity: int
    sku_code: str | None = None
    action: str = ""  # create | set | unchanged | skip
    sku_id: int | None = None
    before: int | None = None
    error: str | None = None


@dataclass
class ImportPlan:
    rows: list[ImportRow] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for r in self.rows:
            counts[r.action] = counts.get(r.action, 0) + 1
        return counts


def _pick(header: list[str], candidates: tuple[str, ...]) -> int | None:
    low = [h.strip().lower() for h in header]
    for c in candidates:
        if c in low:
            return low.index(c)
    return None


def parse_csv(text: str) -> list[ImportRow]:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise CatalogError("the file is empty") from None
    item_i = _pick(header, ITEM_HEADERS)
    qty_i = _pick(header, QTY_HEADERS)
    code_i = _pick(header, ("sku code", "code"))
    if item_i is None or qty_i is None:
        raise CatalogError(
            "need an item column (Item / Product / Name) and a quantity column "
            "(Current Stock / Start of Show Inventory / On hand)"
        )
    rows: list[ImportRow] = []
    for raw in reader:
        if not any(c.strip() for c in raw):
            continue
        item = raw[item_i].strip() if item_i < len(raw) else ""
        qty_s = raw[qty_i].strip() if qty_i < len(raw) else ""
        code = raw[code_i].strip() if code_i is not None and code_i < len(raw) else None
        row = ImportRow(item=item, quantity=0, sku_code=code or None)
        if not item:
            row.action, row.error = "skip", "no item name"
        else:
            try:
                row.quantity = int(float(qty_s.replace(",", ""))) if qty_s else 0
                if row.quantity < 0:
                    raise ValueError
            except ValueError:
                row.action, row.error = "skip", f"bad quantity {qty_s!r}"
        rows.append(row)
    return rows


def plan_import(org_id: int, rows: list[ImportRow]) -> ImportPlan:
    plan = ImportPlan()
    seen: set[str] = set()
    for row in rows:
        if row.action == "skip":
            plan.rows.append(row)
            continue
        key = norm_alias(row.sku_code or row.item)
        if key in seen:
            row.action, row.error = "skip", "duplicate row"
            plan.rows.append(row)
            continue
        seen.add(key)
        sku = resolve_alias(org_id, row.sku_code or row.item)
        if sku is None:
            row.action = "create"
        else:
            row.sku_id, row.before = sku.id, sku.on_hand
            row.action = "set" if sku.on_hand != row.quantity else "unchanged"
        plan.rows.append(row)
    return plan


def apply_import(org_id: int, plan: ImportPlan, *, actor: Actor, import_id: str) -> ImportPlan:
    for i, row in enumerate(plan.rows):
        if row.action == "create":
            product = create_product(
                org_id, row.item, skus=[{"code": row.sku_code or code_from_name(row.item)}]
            )
            sku = product.skus[0]
            row.sku_id, row.before = sku.id, 0
            if row.quantity:
                inventory.move(
                    sku.id,
                    inventory.MovementKind.restock,
                    row.quantity,
                    source_type="import",
                    source_id=f"{import_id}:{i}",
                    reason="spreadsheet import",
                    actor=actor,
                )
        elif row.action == "set":
            inventory.set_on_hand(
                row.sku_id,
                row.quantity,
                source_type="import",
                source_id=f"{import_id}:{i}",
                actor=actor,
                reason="spreadsheet import",
            )
    return plan

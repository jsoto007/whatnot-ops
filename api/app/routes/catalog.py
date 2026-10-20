import uuid

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import serializers
from ..auth import require
from ..db import db
from ..models import Product, Sku
from ..services import audit, catalog, inventory, stock
from ..services.catalog import CatalogError
from ..services.inventory import Actor, InventoryError

bp = Blueprint("catalog", __name__)


@bp.errorhandler(CatalogError)
@bp.errorhandler(InventoryError)
def catalog_error(e):
    db.session.rollback()
    return jsonify({"error": str(e)}), e.status


def _body() -> dict:
    return request.get_json(silent=True) or {}


# ---------- products ----------


@bp.get("/products")
@require("inventory.view")
def list_products():
    rows = db.session.scalars(
        select(Product)
        .where(Product.organization_id == g.u.organization_id)
        .options(selectinload(Product.skus).selectinload(Sku.aliases))
        .order_by(Product.name)
    ).all()
    return jsonify({"products": [serializers.product(p) for p in rows]})


@bp.post("/products")
@require("inventory.write")
def create_product():
    d = _body()
    p = catalog.create_product(
        g.u.organization_id, d.get("name", ""), notes=d.get("notes"), skus=d.get("skus")
    )
    audit.record(
        g.u.organization_id,
        "product.created",
        user=g.u,
        target_type="product",
        target_id=p.id,
        details={"name": p.name},
    )
    db.session.commit()
    return jsonify({"product": serializers.product(p)}), 201


@bp.patch("/products/<int:product_id>")
@require("inventory.write")
def update_product(product_id: int):
    p = catalog.get_product(g.u.organization_id, product_id)
    d = _body()
    before = {"name": p.name, "active": p.active}
    if isinstance(d.get("name"), str) and d["name"].strip():
        p.name = d["name"].strip()[:160]
    if "notes" in d:
        p.notes = d["notes"] if isinstance(d["notes"], str) else None
    if "active" in d:
        p.active = bool(d["active"])
    audit.record(
        g.u.organization_id,
        "product.updated",
        user=g.u,
        target_type="product",
        target_id=p.id,
        details={"before": before, "after": {"name": p.name, "active": p.active}},
    )
    db.session.commit()
    return jsonify({"product": serializers.product(p)})


@bp.post("/products/<int:product_id>/skus")
@require("inventory.write")
def add_sku(product_id: int):
    p = catalog.get_product(g.u.organization_id, product_id)
    d = _body()
    s = catalog.add_sku(
        g.u.organization_id, p, d.get("code", ""), d.get("name", ""), d.get("priceCents")
    )
    db.session.commit()
    return jsonify({"sku": serializers.sku(s)}), 201


@bp.patch("/skus/<int:sku_id>")
@require("inventory.write")
def update_sku(sku_id: int):
    d = _body()
    fields = {k: d[k] for k in ("code", "name", "priceCents", "lowStockAt", "active") if k in d}
    for k in ("code", "name"):
        if k in fields and fields[k] is not None and not isinstance(fields[k], str):
            raise CatalogError(f"{k} must be text")
    if "lowStockAt" in fields and fields["lowStockAt"] is not None:
        try:
            fields["lowStockAt"] = int(fields["lowStockAt"])
        except (TypeError, ValueError):
            raise CatalogError("lowStockAt must be a whole number") from None
    old = catalog.get_sku(g.u.organization_id, sku_id)
    before = {"code": old.code, "priceCents": old.price_cents, "lowStockAt": old.low_stock_at}
    s = catalog.update_sku(g.u.organization_id, sku_id, **fields)
    audit.record(
        g.u.organization_id,
        "sku.updated",
        user=g.u,
        target_type="sku",
        target_id=s.id,
        details={
            "before": before,
            "after": {"code": s.code, "priceCents": s.price_cents, "lowStockAt": s.low_stock_at},
        },
    )
    db.session.commit()
    return jsonify({"sku": serializers.sku(s)})


@bp.post("/skus/<int:sku_id>/aliases")
@require("inventory.write")
def add_alias(sku_id: int):
    s = catalog.get_sku(g.u.organization_id, sku_id)
    alias = _body().get("alias", "")
    a = catalog.add_alias(g.u.organization_id, s, alias if isinstance(alias, str) else "")
    audit.record(
        g.u.organization_id,
        "alias.added",
        user=g.u,
        target_type="sku",
        target_id=s.id,
        details={"alias": a.alias},
    )
    db.session.commit()
    return jsonify({"alias": serializers.alias(a)}), 201


@bp.delete("/aliases/<int:alias_id>")
@require("inventory.write")
def remove_alias(alias_id: int):
    catalog.remove_alias(g.u.organization_id, alias_id)
    audit.record(
        g.u.organization_id,
        "alias.removed",
        user=g.u,
        target_type="alias",
        target_id=alias_id,
    )
    db.session.commit()
    return ("", 204)


# ---------- inventory ----------


@bp.get("/inventory")
@require("inventory.view")
def levels():
    return jsonify({"skus": stock.levels(g.u.organization_id)})


@bp.get("/inventory/<int:sku_id>/movements")
@require("inventory.view")
def movements(sku_id: int):
    catalog.get_sku(g.u.organization_id, sku_id)
    return jsonify({"movements": stock.activity(g.u.organization_id, sku_id=sku_id, limit=200)})


@bp.get("/inventory/activity")
@require("inventory.view")
def activity():
    limit = request.args.get("limit", type=int) or 30
    return jsonify({"movements": stock.activity(g.u.organization_id, limit=limit)})


@bp.post("/inventory/receive")
@require("inventory.write")
def receive():
    d = _body()
    r = stock.receive(
        g.u.organization_id,
        d.get("lines"),
        note=d.get("note"),
        receipt_id=d.get("receiptId"),
        actor=Actor.user(g.u),
    )
    if not r["alreadyApplied"]:
        audit.record(
            g.u.organization_id,
            "inventory.received",
            user=g.u,
            target_type="receipt",
            target_id=r["receiptId"],
            details={
                "units": r["units"],
                "lines": [[x["code"], x["quantity"]] for x in r["lines"]],
                "note": d.get("note"),
            },
        )
    db.session.commit()
    return jsonify(r), 200 if r["alreadyApplied"] else 201


@bp.post("/inventory/count")
@require("inventory.write")
def count():
    d = _body()
    r = stock.count(
        g.u.organization_id,
        d.get("lines"),
        note=d.get("note"),
        count_id=d.get("countId"),
        actor=Actor.user(g.u),
    )
    if r["changed"]:
        audit.record(
            g.u.organization_id,
            "inventory.counted",
            user=g.u,
            target_type="count",
            target_id=r["countId"],
            details={
                "changed": [[x["code"], x["before"], x["after"]] for x in r["lines"] if x["delta"]],
                "note": d.get("note"),
            },
        )
    db.session.commit()
    return jsonify(r)


@bp.post("/inventory/<int:sku_id>/adjust")
@require("inventory.write")
def adjust(sku_id: int):
    catalog.get_sku(g.u.organization_id, sku_id)
    d = _body()
    reason = (d.get("reason") or "").strip() or None
    actor = Actor.user(g.u)
    if "setTo" in d:
        m = inventory.set_on_hand(
            sku_id, int(d["setTo"]), source_type="manual", source_id="", actor=actor, reason=reason
        )
    else:
        try:
            delta = int(d.get("delta"))
        except (TypeError, ValueError):
            raise InventoryError("delta must be a whole number", 400) from None
        kind = inventory.MovementKind.adjust
        m = inventory.move(sku_id, kind, delta, source_type="manual", reason=reason, actor=actor)
    audit.record(
        g.u.organization_id,
        "inventory.adjusted",
        user=g.u,
        target_type="sku",
        target_id=sku_id,
        details={"delta": m.quantity if m else 0, "reason": reason},
    )
    db.session.commit()
    s = catalog.get_sku(g.u.organization_id, sku_id)
    return jsonify({"sku": serializers.sku(s), "movement": serializers.movement(m) if m else None})


@bp.post("/inventory/import")
@require("inventory.write")
def import_sheet():
    d = _body()
    text = d.get("csv") or ""
    if not text.strip():
        return jsonify({"error": "paste or upload a CSV"}), 400
    plan = catalog.plan_import(g.u.organization_id, catalog.parse_csv(text))
    if d.get("apply"):
        import_id = uuid.uuid4().hex[:12]
        catalog.apply_import(g.u.organization_id, plan, actor=Actor.user(g.u), import_id=import_id)
        audit.record(
            g.u.organization_id,
            "inventory.imported",
            user=g.u,
            target_type="import",
            target_id=import_id,
            details=plan.summary,
        )
        db.session.commit()
    else:
        db.session.rollback()
    return jsonify(
        {
            "rows": [serializers.import_row(r) for r in plan.rows],
            "summary": plan.summary,
            "applied": bool(d.get("apply")),
        }
    )

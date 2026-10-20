import uuid

from flask import Blueprint, g, jsonify, request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .. import serializers
from ..auth import require
from ..db import db
from ..models import InventoryMovement, Product, Sku
from ..services import audit, catalog, inventory
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
    if "name" in d and d["name"].strip():
        p.name = d["name"].strip()
    if "notes" in d:
        p.notes = d["notes"]
    if "active" in d:
        p.active = bool(d["active"])
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
    s = catalog.update_sku(g.u.organization_id, sku_id, **_body())
    db.session.commit()
    return jsonify({"sku": serializers.sku(s)})


@bp.post("/skus/<int:sku_id>/aliases")
@require("inventory.write")
def add_alias(sku_id: int):
    s = catalog.get_sku(g.u.organization_id, sku_id)
    a = catalog.add_alias(g.u.organization_id, s, _body().get("alias", ""))
    db.session.commit()
    return jsonify({"alias": serializers.alias(a)}), 201


@bp.delete("/aliases/<int:alias_id>")
@require("inventory.write")
def remove_alias(alias_id: int):
    catalog.remove_alias(g.u.organization_id, alias_id)
    db.session.commit()
    return ("", 204)


# ---------- inventory ----------


@bp.get("/inventory")
@require("inventory.view")
def levels():
    rows = db.session.execute(
        select(Sku, Product.name)
        .join(Product, Product.id == Sku.product_id)
        .where(Sku.organization_id == g.u.organization_id)
        .options(selectinload(Sku.aliases))
        .order_by(Product.name, Sku.code)
    ).all()
    return jsonify({"skus": [{**serializers.sku(s), "productName": pname} for s, pname in rows]})


@bp.get("/inventory/<int:sku_id>/movements")
@require("inventory.view")
def movements(sku_id: int):
    catalog.get_sku(g.u.organization_id, sku_id)
    rows = db.session.scalars(
        select(InventoryMovement)
        .where(InventoryMovement.sku_id == sku_id)
        .order_by(InventoryMovement.id.desc())
        .limit(200)
    ).all()
    return jsonify({"movements": [serializers.movement(m) for m in rows]})


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

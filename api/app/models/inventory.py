import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import db
from .core import utcnow


class Product(db.Model):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_products_org_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    skus: Mapped[list["Sku"]] = relationship(back_populates="product", order_by="Sku.code")


class Sku(db.Model):
    """A sellable unit. on_hand / reserved cache the ledger and change only under a row lock."""

    __tablename__ = "skus"
    __table_args__ = (UniqueConstraint("organization_id", "code", name="uq_skus_org_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    price_cents: Mapped[int | None] = mapped_column(Integer)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_stock_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    product: Mapped[Product] = relationship(back_populates="skus")
    aliases: Mapped[list["ProductAlias"]] = relationship(
        back_populates="sku", cascade="all, delete-orphan"
    )

    @property
    def available(self) -> int:
        """Negative when sales oversold the shelf."""
        return self.on_hand - self.reserved

    @property
    def short(self) -> int:
        """How many to buy to fill every sold order (0 unless oversold)."""
        return max(0, -self.available)


class ProductAlias(db.Model):
    """Free text seen in chat or the sheet ("red bundle", "ultra pack") → SKU."""

    __tablename__ = "product_aliases"
    __table_args__ = (
        UniqueConstraint("organization_id", "alias", name="uq_product_aliases_org_alias"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(160), nullable=False)  # lower-cased, trimmed

    sku: Mapped[Sku] = relationship(back_populates="aliases")


class MovementKind(enum.StrEnum):
    restock = "restock"  # on_hand += q
    adjust = "adjust"  # on_hand += q (q may be negative), manual correction
    reserve = "reserve"  # reserved += q (sale detected)
    release = "release"  # reserved -= q (sale cancelled / payment failed)
    commit = "commit"  # reserved -= q, on_hand -= q (paid)


MOVEMENT_ENUM = Enum(
    MovementKind, name="movement_kind", values_callable=lambda e: [m.value for m in e]
)


class InventoryMovement(db.Model):
    """The ledger. Append-only. (sku, kind, source) is unique so a replayed event moves nothing."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        UniqueConstraint(
            "sku_id", "kind", "source_type", "source_id", name="uq_inventory_movements_source"
        ),
        Index("ix_inventory_movements_sku_created", "sku_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"), nullable=False)
    kind: Mapped[MovementKind] = mapped_column(MOVEMENT_ENUM, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    source_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reason: Mapped[str | None] = mapped_column(String(240))
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    actor_id: Mapped[int | None] = mapped_column(Integer)
    actor_label: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    on_hand_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    sku: Mapped[Sku] = relationship()

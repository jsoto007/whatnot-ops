from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import db
from .core import Show, utcnow


class WhatnotShipment(db.Model):
    """One row of Whatnot's Seller Hub Shipments table, read by the scraper on the seller's
    own laptop. Evidence only: it is compared with the show's order slips and never changes a
    sale, an order or stock. No address is kept, only the recipient's username."""

    __tablename__ = "whatnot_shipments"
    __table_args__ = (
        UniqueConstraint("organization_id", "row_key", name="uq_whatnot_shipments_org_key"),
        Index("ix_whatnot_shipments_show", "show_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    # The show whose date the order date falls on; null when no single show matches.
    show_id: Mapped[int | None] = mapped_column(ForeignKey("shows.id"))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    # sha256 of what identifies the row on the page, so a second read updates it.
    row_key: Mapped[str] = mapped_column(String(64), nullable=False)
    recipient: Mapped[str] = mapped_column(String(80), nullable=False)
    order_date_text: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    ordered_on: Mapped[date | None] = mapped_column(Date)
    item_count: Mapped[int | None] = mapped_column(Integer)
    items_text: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    item_lines: Mapped[list | None] = mapped_column(JSON)
    value_cents: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    tracking: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Whatnot's show filter when the page was read ("<title> Oct 22•1:44 AM EDT"), if sent.
    show_label: Mapped[str] = mapped_column(
        String(300), nullable=False, default="", server_default=""
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    show: Mapped[Show | None] = relationship()


class WhatnotLot(db.Model):
    """One lot inside a Shipments row (one Whatnot order), read from the row's expanded
    detail: the listing title, Whatnot's order number, quantity, price, shipping and sale
    type. With the product the winner picked in chat it makes the show's pick list. Like the
    shipment it is evidence only: it never changes a sale, an order, a payment or stock."""

    __tablename__ = "whatnot_lots"
    __table_args__ = (
        UniqueConstraint("organization_id", "whatnot_order_id", name="uq_whatnot_lots_org_order"),
        Index("ix_whatnot_lots_show", "show_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    show_id: Mapped[int | None] = mapped_column(ForeignKey("shows.id"))
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("whatnot_shipments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "Order #1462657228" on the page; unique per shop, so a second read updates the lot.
    whatnot_order_id: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    lot_number: Mapped[int | None] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price_cents: Mapped[int | None] = mapped_column(Integer)
    shipping_cents: Mapped[int | None] = mapped_column(Integer)
    sale_type: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

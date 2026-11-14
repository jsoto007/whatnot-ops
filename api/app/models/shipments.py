from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import db
from .core import Show, utcnow


class WhatnotShipment(db.Model):
    """One Whatnot shipment (a parcel: everything one buyer's bundle holds). Read either by
    the scraper from Seller Hub's Shipments table on the seller's own laptop, or from the
    Seller API (`whatnot_id` set; services/whatnot/shipping.py), which also knows its
    carrier status, whether a label exists, and what Whatnot wants done first.

    It never changes a sale, a payment or stock. No address is kept, only the recipient's
    username; the label itself (which carries the address) is never stored, only whether
    there is one."""

    __tablename__ = "whatnot_shipments"
    __table_args__ = (
        UniqueConstraint("organization_id", "row_key", name="uq_whatnot_shipments_org_key"),
        Index("ix_whatnot_shipments_show", "show_id"),
        Index(
            "uq_whatnot_shipments_org_whatnot_id",
            "organization_id",
            "whatnot_id",
            unique=True,
            postgresql_where=text("whatnot_id IS NOT NULL"),
        ),
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
    # ---- from the Seller API (all None on a row only the scraper has read) ----
    whatnot_id: Mapped[str | None] = mapped_column(String(20))
    # created | label_generated | dropped_off | in_transit | delivered | failed | cancelled…
    api_status: Mapped[str | None] = mapped_column(String(40))
    carrier: Mapped[str | None] = mapped_column(String(60))
    tracking_code: Mapped[str | None] = mapped_column(String(80))
    # Whatnot has a label (and its combined label + packing slip) to print. Not the link:
    # it is short-lived and shows the buyer's address, so it is fetched when someone prints.
    label_ready: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    dropoff_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What Whatnot wants done before this ships (order_pending_cancellation, …).
    required_actions: Mapped[list | None] = mapped_column(JSON)
    fulfillment_method: Mapped[str | None] = mapped_column(String(16))  # shipping | pickup
    # The shipment that took this one's items (a new label, a re-bundle), if any.
    replaced_by: Mapped[str | None] = mapped_column(String(20))
    url: Mapped[str | None] = mapped_column(String(512))  # its page in Seller Hub
    whatnot_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When someone asked Whatnot, from the app, to make this shipment's label; and why
    # Whatnot refused, as a short code the page turns into words.
    label_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    label_requested_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", name="fk_whatnot_shipments_label_requested_by")
    )
    label_problem: Mapped[str | None] = mapped_column(String(40))
    # When the label was last sent to a printer from here (services/whatnot/shipping.py):
    # "Print labels" skips a package that has one. The time only; never the label.
    label_printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    # From the API, a second and later item of one order is "<order>-2", "<order>-3".
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

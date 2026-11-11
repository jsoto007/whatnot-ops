"""Whatnot Seller API: the shop's connection, the webhook deliveries it sent us, and how
far each sync has read. What the API says about shows, orders and shipments lives next to
this in later migrations; nothing here holds a secret (the token and the webhook signing
secret come from the environment only)."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
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
from .core import Organization, utcnow


class WhatnotConnection(db.Model):
    """Which Whatnot seller this shop is. Made when the owner presses Connect (the app asks
    Whatnot who the server's token belongs to); `disconnected_at` stops every sync and
    makes webhook deliveries for the seller fall on the floor."""

    __tablename__ = "whatnot_connections"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_whatnot_connections_org"),
        UniqueConstraint("seller_id", name="uq_whatnot_connections_seller"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    # Whatnot's user id for the seller (a decimal string); webhooks carry it as seller_id.
    seller_id: Mapped[str] = mapped_column(String(32), nullable=False)
    username: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    connected_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the last call failed, as a short code the page turns into words
    # (token_rejected | no_permission | unreachable); None while calls work.
    problem: Mapped[str | None] = mapped_column(String(32))
    problem_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # What Whatnot's orders may do on their own. "shadow": nothing; a person compares them
    # with the app's sales on the show page and applies them. "auto": they are applied as
    # they arrive (payment status, cancellations, and a sale for an order the app missed).
    orders_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, default="shadow", server_default="shadow"
    )

    organization: Mapped[Organization] = relationship()

    @property
    def active(self) -> bool:
        return self.disconnected_at is None


class WhatnotEvent(db.Model):
    """One webhook delivery, after its signature was checked. Only what is needed to act on
    it is kept: the topic, when it happened and the ids it names. The body is not stored
    (it can carry a buyer's details); handlers read the resource again from the API.

    `webhook_id` is Whatnot's id for the event. Retries send the same one, so the unique
    index is what makes a delivery count once."""

    __tablename__ = "whatnot_events"
    __table_args__ = (
        UniqueConstraint("webhook_id", name="uq_whatnot_events_webhook_id"),
        Index("ix_whatnot_events_org_received", "organization_id", "received_at"),
        Index("ix_whatnot_events_status", "status", "received_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    webhook_id: Mapped[str] = mapped_column(String(80), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    # The ids the event names ({"order_id": …}, {"shipment_id": …, "replaced_by": …}).
    ref: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    # queued → done | ignored (a topic nothing handles yet) | failed
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(240))


class WhatnotSyncState(db.Model):
    """How far one kind of reading has got for a shop ("orders", "shipments", "shows", …):
    when it last ran, when it last worked, and where to pick up."""

    __tablename__ = "whatnot_sync_state"
    __table_args__ = (
        UniqueConstraint("organization_id", "resource", name="uq_whatnot_sync_org_resource"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    resource: Mapped[str] = mapped_column(String(40), nullable=False)
    cursor: Mapped[dict | None] = mapped_column(JSON)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(240))
    counts: Mapped[dict | None] = mapped_column(JSON)


class WhatnotShow(db.Model):
    """A show as Whatnot knows it (GET /shows). A recording made by the extension (`shows`)
    points at the Whatnot show it is a recording of: `Show.whatnot_show_id`."""

    __tablename__ = "whatnot_shows"
    __table_args__ = (
        UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_shows_org_id"),
        Index("ix_whatnot_shows_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    whatnot_id: Mapped[str] = mapped_column(String(40), nullable=False)  # a UUID
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    # scheduled | live | stopped (can resume) | ended | cancelled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When it went live, as far as we know: the `shows.started` event, else its first order.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Its page in Seller Hub (a whatnot.com link, checked before it is kept).
    url: Mapped[str | None] = mapped_column(String(512))
    # When every order since the show began was last read; None before the first full read.
    orders_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    @property
    def begins_at(self) -> datetime | None:
        """The earliest moment the show can have been live."""
        known = [t for t in (self.started_at, self.scheduled_start_at) if t is not None]
        return min(known) if known else None


class WhatnotOrder(db.Model):
    """One order as Whatnot has it. In a live show each win is its own order. Whatnot's
    word on whether it was paid: `status` is pending (not ready to fulfil), then
    pending_fulfillment, completed, cancelled or failed.

    Only the buyer's id and username are kept; no address, email or phone."""

    __tablename__ = "whatnot_orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_orders_org_id"),
        Index("ix_whatnot_orders_org_show", "organization_id", "whatnot_show_id"),
        Index("ix_whatnot_orders_org_status", "organization_id", "status", "ordered_at"),
        Index("ix_whatnot_orders_org_buyer", "organization_id", text("lower(buyer_username)")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    whatnot_id: Mapped[str] = mapped_column(String(20), nullable=False)
    # Whatnot's id of the show it was placed in; None for a profile-shop order.
    whatnot_show_id: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    transaction_type: Mapped[str | None] = mapped_column(String(16))
    buyer_id: Mapped[str | None] = mapped_column(String(32))
    buyer_username: Mapped[str | None] = mapped_column(String(80))
    subtotal_cents: Mapped[int | None] = mapped_column(Integer)
    shipping_cents: Mapped[int | None] = mapped_column(Integer)
    tax_cents: Mapped[int | None] = mapped_column(Integer)
    discount_cents: Mapped[int | None] = mapped_column(Integer)
    total_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    ordered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    whatnot_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    items: Mapped[list["WhatnotOrderItem"]] = relationship(
        back_populates="order", order_by="WhatnotOrderItem.id", cascade="all, delete-orphan"
    )


class WhatnotOrderItem(db.Model):
    """One line of a Whatnot order: the listing as it was bought. `sale_id` is the app's
    sale it turned out to be (set when the two are matched)."""

    __tablename__ = "whatnot_order_items"
    __table_args__ = (
        UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_order_items_org_id"),
        Index("ix_whatnot_order_items_order", "order_id"),
        Index("ix_whatnot_order_items_sale", "sale_id"),
        Index("ix_whatnot_order_items_shipment", "organization_id", "whatnot_shipment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("whatnot_orders.id", ondelete="CASCADE"), nullable=False
    )
    whatnot_id: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    sku_code: Mapped[str | None] = mapped_column(String(80))
    product_id: Mapped[str | None] = mapped_column(String(20))
    variant_id: Mapped[str | None] = mapped_column(String(40))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    unit_price_cents: Mapped[int | None] = mapped_column(Integer)
    # The Whatnot shipment (parcel) holding this item right now, if any.
    whatnot_shipment_id: Mapped[str | None] = mapped_column(String(20))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id", ondelete="SET NULL"))

    order: Mapped[WhatnotOrder] = relationship(back_populates="items")

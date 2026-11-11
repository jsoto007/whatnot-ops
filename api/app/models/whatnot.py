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
    # Whatnot's own results for the show (viewers, reach, buyers, sales), money in cents.
    # `analytics_status` is Whatnot's word on them: GENERATED, PENDING (still being worked
    # out), NOT_ENDED, or WILL_NOT_BE_CALCULATED (cancelled, private or a rehearsal).
    analytics: Mapped[dict | None] = mapped_column(JSON)
    analytics_status: Mapped[str | None] = mapped_column(String(24))
    analytics_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class WhatnotVariant(db.Model):
    """One variant of a product in the shop's Whatnot inventory (a listing as it can be
    bought), and the product here it is the same thing as (`sku_id`), when known.

    `link` says who decided: "code" (Whatnot's SKU equals a product code here), "person",
    or "off" (a person said it is none of ours: never linked again by code, and its title
    names no product either). `available` is Whatnot's own count as last read. The app
    never changes it."""

    __tablename__ = "whatnot_variants"
    __table_args__ = (
        UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_variants_org_id"),
        Index("ix_whatnot_variants_org_sku", "organization_id", "sku_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    whatnot_id: Mapped[str] = mapped_column(String(40), nullable=False)
    product_id: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    options: Mapped[str] = mapped_column(String(240), nullable=False, default="")  # "Red / L"
    sku_code: Mapped[str | None] = mapped_column(String(80))
    external_id: Mapped[str | None] = mapped_column(String(120))
    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    available: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str | None] = mapped_column(String(512))
    sku_id: Mapped[int | None] = mapped_column(ForeignKey("skus.id", ondelete="SET NULL"))
    link: Mapped[str | None] = mapped_column(String(8))  # code | person | off
    linked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Set when a full read of the catalog no longer finds it (deleted on Whatnot).
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class WhatnotReport(db.Model):
    """A report the app asked Whatnot to build (it is made in the background and then
    offers a CSV to download). Today: the financial ledger for a span of days."""

    __tablename__ = "whatnot_reports"
    __table_args__ = (
        UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_reports_org_id"),
        Index("ix_whatnot_reports_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    whatnot_id: Mapped[str] = mapped_column(String(40), nullable=False)
    report_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # pending | in_progress | completed | failed | cancelled (Whatnot's), then "imported"
    # once its file is in the ledger, or "unreadable" when the file was not what we expect.
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows: Mapped[int | None] = mapped_column(Integer)
    # Lines of the file that are NOT in the ledger, so the page can say the figures are
    # short: `skipped` had no readable id or amount; `repeated` shared a transaction id
    # with an earlier line of the same file (only the first of each is kept).
    skipped: Mapped[int | None] = mapped_column(Integer)
    repeated: Mapped[int | None] = mapped_column(Integer)


class WhatnotLedgerEntry(db.Model):
    """One line of Whatnot's financial ledger: what it credited or charged the seller, and
    for an order's earnings the fees it took. Amounts are integer cents, signed as Whatnot
    signs them (a charge or a refund is negative). `ledger_transaction_id` is Whatnot's id
    for the line: importing a report again, or an overlapping one, changes nothing.

    Read-only bookkeeping: nothing here changes a sale, an order or stock. Of the buyer
    only the username is kept."""

    __tablename__ = "whatnot_ledger"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "ledger_transaction_id", name="uq_whatnot_ledger_org_txn"
        ),
        Index("ix_whatnot_ledger_org_show", "organization_id", "whatnot_show_id"),
        Index("ix_whatnot_ledger_org_completed", "organization_id", "completed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    ledger_transaction_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # ORDER_EARNINGS, SHIPPING_CHARGE, CARRIER_ADJUSTMENT, ORDER_REFUND, TIP, …
    transaction_type: Mapped[str] = mapped_column(String(48), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    whatnot_order_id: Mapped[str | None] = mapped_column(String(20))
    whatnot_show_id: Mapped[str | None] = mapped_column(String(40))
    whatnot_shipment_id: Mapped[str | None] = mapped_column(String(20))
    listing_title: Mapped[str | None] = mapped_column(String(240))
    sku_code: Mapped[str | None] = mapped_column(String(80))
    buy_format: Mapped[str | None] = mapped_column(String(24))
    quantity: Mapped[int | None] = mapped_column(Integer)
    buyer_username: Mapped[str | None] = mapped_column(String(80))
    currency: Mapped[str | None] = mapped_column(String(3))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    buyer_paid_cents: Mapped[int | None] = mapped_column(Integer)
    item_price_cents: Mapped[int | None] = mapped_column(Integer)
    coupon_cents: Mapped[int | None] = mapped_column(Integer)
    seller_shipping_cents: Mapped[int | None] = mapped_column(Integer)
    commission_cents: Mapped[int | None] = mapped_column(Integer)
    commission_tax_cents: Mapped[int | None] = mapped_column(Integer)
    processing_fee_cents: Mapped[int | None] = mapped_column(Integer)
    processing_fee_tax_cents: Mapped[int | None] = mapped_column(Integer)
    cost_of_goods_cents: Mapped[int | None] = mapped_column(Integer)
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatnot_reports.id", ondelete="SET NULL")
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

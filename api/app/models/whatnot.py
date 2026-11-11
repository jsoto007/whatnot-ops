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

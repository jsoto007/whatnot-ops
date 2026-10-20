import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import db
from .core import ChatMessage, Show, utcnow
from .inventory import Sku


class EventKind(enum.StrEnum):
    sale = "sale"  # the platform or host confirmed a purchase
    claim = "claim"  # a buyer says "mine" / "I'll take 2" (evidence, not a sale)
    gift = "gift"  # giveaway / free item
    cancel = "cancel"
    payment_failed = "payment_failed"
    no_pick = "no_pick"  # auction ended without a winner
    listing = "listing"  # host announces the item up for auction
    chatter = "chatter"


EVENT_ENUM = Enum(EventKind, name="event_kind", values_callable=lambda e: [m.value for m in e])


class SaleStatus(enum.StrEnum):
    detected = "detected"  # seen, not reserved (unmatched item or no stock)
    reserved = "reserved"
    paid = "paid"
    cancelled = "cancelled"
    payment_failed = "payment_failed"
    fulfilled = "fulfilled"


SALE_STATUS_ENUM = Enum(
    SaleStatus, name="sale_status", values_callable=lambda e: [m.value for m in e]
)


class ParsedEvent(db.Model):
    """What one chat line means. One per message; re-parsing replaces events that led nowhere."""

    __tablename__ = "parsed_events"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_parsed_events_message"),
        Index("ix_parsed_events_show_kind", "show_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), nullable=False)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), nullable=False)
    kind: Mapped[EventKind] = mapped_column(EVENT_ENUM, nullable=False)
    buyer_username: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    item_text: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price_cents: Mapped[int | None] = mapped_column(Integer)
    auction_type: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    rule: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    message: Mapped[ChatMessage] = relationship()
    show: Mapped[Show] = relationship()


class Sale(db.Model):
    __tablename__ = "sales"
    __table_args__ = (
        Index("ix_sales_show_buyer", "show_id", "buyer_username"),
        Index("ix_sales_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), nullable=False)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("parsed_events.id"), unique=True)
    buyer_username: Mapped[str] = mapped_column(String(80), nullable=False)
    sku_id: Mapped[int | None] = mapped_column(ForeignKey("skus.id"), index=True)
    item_text: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    price_cents: Mapped[int | None] = mapped_column(Integer)
    auction_type: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    status: Mapped[SaleStatus] = mapped_column(
        SALE_STATUS_ENUM, nullable=False, default=SaleStatus.detected
    )
    status_reason: Mapped[str | None] = mapped_column(String(240))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    external_order_id: Mapped[str | None] = mapped_column(String(80))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flagged: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    show: Mapped[Show] = relationship()
    sku: Mapped[Sku | None] = relationship()
    event: Mapped[ParsedEvent | None] = relationship()
    order: Mapped["Order | None"] = relationship(back_populates="sales")  # noqa: F821
    order: Mapped["Order | None"] = relationship(back_populates="sales")  # noqa: F821


class ReviewStatus(enum.StrEnum):
    open = "open"
    resolved = "resolved"
    dismissed = "dismissed"


REVIEW_STATUS_ENUM = Enum(
    ReviewStatus, name="review_status", values_callable=lambda e: [m.value for m in e]
)


class ReviewItem(db.Model):
    """Something a person must decide. Rules, inventory gates and (Phase 6) AI all land here."""

    __tablename__ = "review_items"
    __table_args__ = (Index("ix_review_items_org_status", "organization_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    show_id: Mapped[int | None] = mapped_column(ForeignKey("shows.id"))
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("parsed_events.id"))
    sale_id: Mapped[int | None] = mapped_column(ForeignKey("sales.id"))
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    proposal: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="rule")
    status: Mapped[ReviewStatus] = mapped_column(
        REVIEW_STATUS_ENUM, nullable=False, default=ReviewStatus.open
    )
    resolution: Mapped[dict | None] = mapped_column(JSON)
    resolved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    message: Mapped[ChatMessage | None] = relationship()
    sale: Mapped[Sale | None] = relationship()


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


JOB_STATUS_ENUM = Enum(JobStatus, name="job_status", values_callable=lambda e: [m.value for m in e])


class Job(db.Model):
    """Postgres-backed queue: workers claim with SELECT … FOR UPDATE SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_at", "status", "run_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[JobStatus] = mapped_column(
        JOB_STATUS_ENUM, nullable=False, default=JobStatus.queued
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

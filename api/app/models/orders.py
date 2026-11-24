import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
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
from .core import Show, utcnow


class OrderStatus(enum.StrEnum):
    open = "open"  # a standing sale still needs a person (item not recognised)
    paid = "paid"  # every standing sale is paid → ready to pick
    picking = "picking"
    packed = "packed"
    shipped = "shipped"
    cancelled = "cancelled"  # no standing sales left


ORDER_STATUS_ENUM = Enum(
    OrderStatus, name="order_status", values_callable=lambda e: [m.value for m in e]
)
FULFILLMENT = (OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped)


class Order(db.Model):
    """One buyer's purchases in one show; status follows the sales until fulfilment starts."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("show_id", "buyer_username", name="uq_orders_show_buyer"),
        Index("ix_orders_org_status", "organization_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), nullable=False)
    buyer_username: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        ORDER_STATUS_ENUM, nullable=False, default=OrderStatus.open
    )
    notes: Mapped[str | None] = mapped_column(Text)
    tracking: Mapped[str | None] = mapped_column(String(120))
    picked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    packed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "Packed by" on the shop's paper slip: the packer's name or initials.
    packed_by: Mapped[str | None] = mapped_column(String(80))
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    show: Mapped[Show] = relationship()
    sales: Mapped[list["Sale"]] = relationship(back_populates="order", order_by="Sale.id")  # noqa: F821


class PackOverride(db.Model):
    """A manager's word that a buyer's box in one show may be packed although something about
    it still waits for a person (the Orders page's "Mark ready to pack", `order_board`). It
    covers what was open when it was given: anything that opens later (a new win, a decline,
    another giveaway) puts the buyer back in Need review. It changes no order, sale or stock,
    and no gate: the app still refuses to start picking a box whose card is in doubt."""

    __tablename__ = "pack_overrides"
    __table_args__ = (
        UniqueConstraint("show_id", "buyer_key", name="uq_pack_overrides_show_buyer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    # A show folded into another or deleted takes its overrides with it.
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"), nullable=False)
    # The buyer's name in lower case without "@" (`order_board.key`).
    buyer_key: Mapped[str] = mapped_column(String(80), nullable=False)
    # What held the box when it was marked (`order_board.Buyer.keys`): anything else undoes it.
    covered: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

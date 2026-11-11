"""Who buys from the shop: one row per person the shop knows something about.

A profile is made when Whatnot names a buyer's account id (an order read through the
Seller API) or when someone on the team flags or writes a note about a buyer. What a
buyer bought is never stored here: it is worked out from the shop's sales, by name.

Only Whatnot's user id and the usernames are kept. No address, email or phone."""

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import db
from .core import utcnow


class Buyer(db.Model):
    __tablename__ = "buyers"
    __table_args__ = (
        # Whatnot's id is what makes two names one person. NULL (a profile the team made
        # for a name Whatnot has not told us about) never collides.
        UniqueConstraint("organization_id", "whatnot_user_id", name="uq_buyers_org_whatnot_user"),
        Index("ix_buyers_org_flag", "organization_id", "flag"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    whatnot_user_id: Mapped[str | None] = mapped_column(String(32))
    # The name they go by now, as last written by Whatnot (or as the team typed it).
    username: Mapped[str] = mapped_column(String(80), nullable=False)
    # When Whatnot last used that name on an order: an older order read late never
    # brings an old name back.
    username_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    flag: Mapped[str | None] = mapped_column(String(8))  # vip | watch
    note: Mapped[str | None] = mapped_column(Text)
    noted_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    noted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class BuyerName(db.Model):
    """A username a buyer has used. A name belongs to one profile at a time; sales are
    counted for whoever holds the name they were made under."""

    __tablename__ = "buyer_names"
    __table_args__ = (
        UniqueConstraint("organization_id", "name_lower", name="uq_buyer_names_org_name"),
        Index("ix_buyer_names_buyer", "buyer_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    buyer_id: Mapped[int] = mapped_column(
        ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False
    )
    name_lower: Mapped[str] = mapped_column(String(80), nullable=False)
    username: Mapped[str] = mapped_column(String(80), nullable=False)  # as written
    # When Whatnot last used this name for this buyer. A name that later shows up on a
    # different account moves to it only when that sighting is the newer one.
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the name came to this profile from a different Whatnot account: the show
    # date of the order that moved it. What was won under the name before that date was
    # the earlier account's, and is not counted for this buyer.
    since_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

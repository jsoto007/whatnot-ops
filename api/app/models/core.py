from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import db


def utcnow() -> datetime:
    return datetime.now(UTC)


class Organization(db.Model):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint(
            "sale_mode IN ('winners_pick', 'listing')", name="ck_organizations_sale_mode"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The seller's Whatnot handle: chat lines from this user count as host confirmations.
    host_username: Mapped[str | None] = mapped_column(String(80))
    # How this shop usually sells (services/sale_mode.py): "winners_pick" (the winner names
    # the product in chat) or "listing" (the listing is the product). What a new show starts
    # in; a show can differ (ShowSaleMode).
    sale_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="winners_pick", server_default="winners_pick"
    )
    # Whether a winner's pick names the product with no person (services/picks.py): the
    # shelf spot the win's own buyer typed, applied ten minutes after the show ends. Off
    # (the default): the pick is only suggested, and what would have been assigned is kept
    # on the review item, so a real show can be compared before this is switched on.
    auto_pick: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Whatnot names whose chat lines may set an offer on a lot ("#45 is two for one"), besides
    # the host (services/offers.py). Lower case, plain ASCII.
    trusted_mods: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    devices: Mapped[list["Device"]] = relationship(back_populates="organization")
    shows: Mapped[list["Show"]] = relationship(back_populates="organization")


class Device(db.Model):
    """A laptop running the extension; authenticates ingest with an API key (hash stored only)."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The Chat Scraper version it last called in with (X-Extension-Version); None before 1.5.0.
    extension_version: Mapped[str | None] = mapped_column(String(16))
    # The person who made the key (in Settings, or the pairing code's creator). The key is
    # revoked when that person is disabled or can no longer manage laptops
    # (accounts._withdraw_grants). None for keys made before 0018 or from the CLI.
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    organization: Mapped[Organization] = relationship(back_populates="devices")
    created_by: Mapped["User | None"] = relationship()  # noqa: F821 (models/accounts.py)

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class DevicePairing(db.Model):
    """A short-lived, single-use code a laptop trades for its device key.

    Only the code's SHA-256 is stored. `used_at` is set by one conditional UPDATE,
    so two laptops racing on the same code cannot both get a key.
    """

    __tablename__ = "device_pairings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))

    device: Mapped["Device | None"] = relationship()


class Show(db.Model):
    __tablename__ = "shows"
    __table_args__ = (UniqueConstraint("organization_id", "show_key", name="uq_shows_org_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id"), nullable=False, index=True
    )
    show_key: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    show_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String(512))
    # The Whatnot show this is a recording of (models/whatnot.py), once known. Several
    # recordings can be of one show (two laptops, or recording restarted). `whatnot_link`
    # says who decided: "auto" (matched by time) may be re-decided, "manual" is a person's.
    whatnot_show_id: Mapped[int | None] = mapped_column(
        ForeignKey("whatnot_shows.id", ondelete="SET NULL", name="fk_shows_whatnot_show_id"),
        index=True,
    )
    whatnot_link: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    organization: Mapped[Organization] = relationship(back_populates="shows")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="show")
    whatnot_show: Mapped["WhatnotShow | None"] = relationship()  # noqa: F821 (models/whatnot.py)


class ShowShelf(db.Model):
    """Where a product sits for one show: "B4" is shelf B, spot 4 (services/shelves.py). A
    table of its own, so saving a layout never touches the show's `updated_at` (read as the
    time chat last arrived)."""

    __tablename__ = "show_shelves"

    show_id: Mapped[int] = mapped_column(
        ForeignKey("shows.id", ondelete="CASCADE"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(4), primary_key=True)
    sku_id: Mapped[int] = mapped_column(ForeignKey("skus.id"), nullable=False)


class ShowSaleMode(db.Model):
    """A show that sells differently from the shop's usual way (services/sale_mode.py). A
    table of its own for the reason ShowShelf is one: changing it never touches the show's
    `updated_at` (read as the time chat last arrived)."""

    __tablename__ = "show_sale_modes"
    __table_args__ = (
        CheckConstraint("mode IN ('winners_pick', 'listing')", name="ck_show_sale_modes_mode"),
    )

    show_id: Mapped[int] = mapped_column(
        ForeignKey("shows.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)


class ChatMessage(db.Model):
    """One raw chat line, immutable. client_id is the extension's row id (idempotency key).

    source is "chat" for a line typed in the chat, "player" for a win the extension read
    from Whatnot's video player (viewers cannot write there; see rules.classify)."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("show_id", "client_id", name="uq_chat_messages_show_client"),
        Index("ix_chat_messages_show_seq", "show_id", "seq"),
        Index("ix_chat_messages_show_username", "show_id", "username"),
        Index("ix_chat_messages_show_captured", "show_id", "captured_at"),
        # The parser's "same chat line again, maybe from the other laptop" check looks across
        # the shop's shows by sender and time (pipeline._same_chat_line_near).
        Index("ix_chat_messages_username_captured", "username", "captured_at"),
        CheckConstraint("source IN ('chat', 'player')", name="ck_chat_messages_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), nullable=False)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    client_id: Mapped[str] = mapped_column(String(16), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(8), nullable=False, default="chat", server_default="chat"
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    show: Mapped[Show] = relationship(back_populates="messages")


class RateLimit(db.Model):
    """Fixed-window attempt counters (services/ratelimit.py). Shared by every gunicorn
    worker because it lives in Postgres; rows older than a day are pruned."""

    __tablename__ = "rate_limits"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

from datetime import UTC, date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import db


def utcnow() -> datetime:
    return datetime.now(UTC)


class Organization(db.Model):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The seller's Whatnot handle: chat lines from this user count as host confirmations.
    host_username: Mapped[str | None] = mapped_column(String(80))
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

    organization: Mapped[Organization] = relationship(back_populates="devices")

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    organization: Mapped[Organization] = relationship(back_populates="shows")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="show")


class ChatMessage(db.Model):
    """One raw chat line, immutable. client_id is the extension's row id (idempotency key)."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("show_id", "client_id", name="uq_chat_messages_show_client"),
        Index("ix_chat_messages_show_seq", "show_id", "seq"),
        Index("ix_chat_messages_show_username", "show_id", "username"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id"), nullable=False)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id"))
    client_id: Mapped[str] = mapped_column(String(16), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    show: Mapped[Show] = relationship(back_populates="messages")

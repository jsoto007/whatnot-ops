import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from ..db import db
from ..models import Device, Organization

KEY_PREFIX = "wn_"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def create_device(org: Organization, name: str, *, key: str | None = None) -> tuple[Device, str]:
    """Create a device and return it with its plaintext key (shown once, never stored)."""
    key = key or generate_key()
    device = Device(organization=org, name=name, key_hash=hash_key(key), key_prefix=key[:10])
    db.session.add(device)
    db.session.flush()
    return device, key


def device_for_key(key: str | None) -> Device | None:
    if not key or not key.startswith(KEY_PREFIX):
        return None
    device = db.session.scalar(select(Device).where(Device.key_hash == hash_key(key)))
    if device is None or not device.active:
        return None
    return device


def touch(device: Device) -> None:
    device.last_seen_at = datetime.now(UTC)

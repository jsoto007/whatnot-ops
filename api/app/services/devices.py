import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

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


# Chrome's own version format: one to four dot-separated integers.
VERSION_RE = re.compile(r"\d{1,5}(\.\d{1,5}){0,3}")


def touch(device: Device, extension_version: str | None = None) -> None:
    """Seen just now. Keeps the extension version it reported, when it is a real version;
    builds before 1.5.0 send none and leave it as it was."""
    device.last_seen_at = datetime.now(UTC)
    v = (extension_version or "").strip()
    if len(v) <= 16 and VERSION_RE.fullmatch(v) and device.extension_version != v:
        device.extension_version = v


# ---------- pairing codes ----------
#
# The portal shows a short code; the laptop's extension sends it to /api/pair
# and gets its device key back, so nobody copies a 40-character key by hand.
# 8 characters from a 31-letter alphabet (no 0/O, 1/I/L) is ~8.5e11 codes; a
# code lives 10 minutes, is single-use, and an org may hold 5 open at once, so
# guessing one is not a practical attack.

PAIR_MINUTES = 10
MAX_OPEN_PAIRINGS = 5
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class PairingError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def normalise_code(code: str | None) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def format_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def start_pairing(org: Organization, name: str, *, user=None):
    """A new code for one laptop. Returns (pairing, display code); the code is shown once."""
    from ..models import DevicePairing

    name = (name or "").strip()[:120]
    if not name:
        raise PairingError("give the laptop a name")
    now = datetime.now(UTC)
    open_count = db.session.scalar(
        select(func.count())
        .select_from(DevicePairing)
        .where(
            DevicePairing.organization_id == org.id,
            DevicePairing.used_at.is_(None),
            DevicePairing.expires_at > now,
        )
    )
    if (open_count or 0) >= MAX_OPEN_PAIRINGS:
        raise PairingError("too many open codes; use one or wait 10 minutes", 429)
    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    pairing = DevicePairing(
        organization_id=org.id,
        name=name,
        code_hash=hash_key(code),
        created_by_id=getattr(user, "id", None),
        created_at=now,
        expires_at=now + timedelta(minutes=PAIR_MINUTES),
    )
    db.session.add(pairing)
    db.session.flush()
    return pairing, format_code(code)


def redeem_pairing(code: str | None):
    """Trade a code for a new device and its key. Exactly one caller can win a code:
    the conditional UPDATE's RETURNING decides, never a read-then-write."""
    from ..models import DevicePairing

    norm = normalise_code(code)
    if len(norm) != 8:
        raise PairingError("enter the 8-character code shown in the web app")
    now = datetime.now(UTC)
    won = db.session.execute(
        update(DevicePairing)
        .where(
            DevicePairing.code_hash == hash_key(norm),
            DevicePairing.used_at.is_(None),
            DevicePairing.expires_at > now,
        )
        .values(used_at=now)
        .returning(DevicePairing.id)
    ).scalar_one_or_none()
    if won is None:
        raise PairingError(
            "that code is wrong, used or expired; make a new one in the web app", 404
        )
    pairing = db.session.get(DevicePairing, won)
    org = db.session.get(Organization, pairing.organization_id)
    device, key = create_device(org, pairing.name)
    pairing.device_id = device.id
    return pairing, device, key

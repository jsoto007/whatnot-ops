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


def create_device(
    org: Organization,
    name: str,
    *,
    key: str | None = None,
    created_by=None,
    created_by_id: int | None = None,
) -> tuple[Device, str]:
    """Create a device and return it with its plaintext key (shown once, never stored).

    `created_by` is the signed-in person making it in Settings, checked here under a
    share lock on their row (`lock_maker`). `created_by_id` is a pairing code's
    creator, already checked under that lock by `redeem_pairing`. The key dies with
    that person's access (accounts._withdraw_grants)."""
    if created_by is not None:
        lock_maker(created_by)
        created_by_id = created_by.id
    key = key or generate_key()
    device = Device(
        organization=org,
        name=name,
        key_hash=hash_key(key),
        key_prefix=key[:10],
        created_by_id=created_by_id,
    )
    db.session.add(device)
    db.session.flush()
    return device, key


def lock_maker(user) -> None:
    """Refuse unless the person handing out a key or a code may still manage laptops."""
    if not hold_maker(user.id, user.organization_id):
        raise PairingError("you can no longer connect laptops", 403)


def hold_maker(user_id: int, org_id: int) -> bool:
    """Whether this person may still hand out laptop keys, read under a FOR SHARE lock
    on their user row that is held until this transaction ends.

    This is what keeps a key from outliving its maker's access (security finding B1).
    A disable or demotion UPDATEs that row (FOR NO KEY UPDATE; an owner's row is even
    locked FOR UPDATE first by accounts._lock_owners), which conflicts with FOR SHARE.
    So either the change waits for this hand-out to commit and then revokes what it
    made (accounts._withdraw_grants), or this waits for the change and sees it. Only
    FOR SHARE (or stronger) works: FOR KEY SHARE does not conflict with that UPDATE.
    tests/test_security.py pins the conflict."""
    from ..models import User
    from .permissions import allowed

    row = db.session.execute(
        select(User.disabled, User.role)
        .where(User.id == user_id, User.organization_id == org_id)
        .with_for_update(read=True)
    ).one_or_none()
    return row is not None and not row.disabled and allowed(row.role, "devices.manage")


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
    if user is not None:
        lock_maker(user)  # a code made as its maker is disabled would outlive the disable
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
    the conditional UPDATE's RETURNING decides, never a read-then-write.

    The laptop belongs to whoever made the code, and its key dies with that person's
    access. Before the code is spent, the creator is checked under `hold_maker`'s
    share lock, held until commit: a disable that commits first is seen here and the
    code refused (unspent); one that comes later waits for this commit and then
    revokes the new key. As a second guard, the disable expires every unused code of
    the person, so it also queues behind a code won here but not yet committed."""
    from ..models import DevicePairing

    norm = normalise_code(code)
    if len(norm) != 8:
        raise PairingError("enter the 8-character code shown in the web app")
    code_hash = hash_key(norm)
    wrong = PairingError("that code is wrong, used or expired; make a new one in the web app", 404)
    # Only an open code locks its creator's row: a used or expired one costs no lock.
    open_code = db.session.execute(
        select(DevicePairing.created_by_id, DevicePairing.organization_id).where(
            DevicePairing.code_hash == code_hash,
            DevicePairing.used_at.is_(None),
            DevicePairing.expires_at > datetime.now(UTC),
        )
    ).one_or_none()
    if open_code is None:
        raise wrong
    if open_code.created_by_id is not None and not hold_maker(
        open_code.created_by_id, open_code.organization_id
    ):
        raise wrong
    now = datetime.now(UTC)
    won = db.session.execute(
        update(DevicePairing)
        .where(
            DevicePairing.code_hash == code_hash,
            DevicePairing.used_at.is_(None),
            DevicePairing.expires_at > now,
        )
        .values(used_at=now)
        .returning(DevicePairing.id)
    ).scalar_one_or_none()
    if won is None:
        raise wrong
    pairing = db.session.get(DevicePairing, won)
    org = db.session.get(Organization, pairing.organization_id)
    device, key = create_device(org, pairing.name, created_by_id=pairing.created_by_id)
    pairing.device_id = device.id
    return pairing, device, key

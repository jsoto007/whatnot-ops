import hashlib
import logging
import os
import re
import secrets
import threading
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from flask import current_app
from sqlalchemy import func, select, update

from ..config import is_dev
from ..db import db
from ..models import Invite, Organization, PasswordReset, Role, User
from . import audit, mail
from .admin import admin_email, is_admin, is_protected

# OWASP's argon2id minimum (19 MiB, t=2, p=1): the 64 MiB library default let a
# handful of concurrent logins exhaust a 512 MB instance. Older hashes are
# upgraded on the next successful login (check_needs_rehash).
log = logging.getLogger(__name__)

_ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_hash_slots = threading.BoundedSemaphore(2)  # per process: at most 2 hashes at once
MAX_FAILED_LOGINS = 8  # per email + client address, per window
FAIL_WINDOW_S = 15 * 60
MAX_FAILS_PER_EMAIL = 100  # per hour from all addresses: caps distributed guessing
INVITE_DAYS = 7
RESET_HOURS = 2
MIN_PASSWORD = 10
MAX_PASSWORD = 256
MAX_EMAIL = 254


class AccountError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    """Linear-time check (the old regex backtracked quadratically on long input)."""
    email = (email if isinstance(email, str) else "").strip().lower()
    if len(email) > MAX_EMAIL or email.count("@") != 1 or any(c.isspace() for c in email):
        raise AccountError("enter a valid email address")
    local, domain = email.split("@")
    if not local or "." not in domain[1:-1]:
        raise AccountError("enter a valid email address")
    return email


def check_password_strength(password: str) -> None:
    if not isinstance(password, str) or len(password.strip()) < MIN_PASSWORD:
        raise AccountError(f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"password must be at most {MAX_PASSWORD} characters")


def hash_password(password: str) -> str:
    with _hash_slots:
        return _ph.hash(password)


def verify_password(stored: str, password: str) -> bool:
    """False for a wrong password and for a stored value that is not an argon2 hash
    (a hand-edited row) instead of raising."""
    try:
        return _verify(stored, password)
    except (InvalidHashError, VerificationError):
        return False


def _verify(stored: str, password: str) -> bool:
    with _hash_slots:
        try:
            return _ph.verify(stored, password)
        except VerifyMismatchError:
            return False


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "shop"
    slug, n = base, 2
    while db.session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None:
        slug = f"{base}-{n}"
        n += 1
    return slug


def app_url(path: str) -> str:
    base = (
        os.environ.get("APP_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or "http://localhost:5173"
    )
    return base.rstrip("/") + path


# ---------- sign-up (only the ADMIN_EMAIL, only once) ----------


def setup_token() -> str | None:
    return (os.environ.get("SETUP_TOKEN") or "").strip() or None


def setup_code_required() -> bool:
    """Creating the shop needs the one-time SETUP_TOKEN from the server's environment:
    always in production, and in development whenever one is set. Knowing (or
    guessing) the admin's email is not enough to claim the shop."""
    return setup_token() is not None or not is_dev(current_app.config.get("APP_ENV"))


def signup_state() -> tuple[bool, bool]:
    """(open, admin_only). See services/admin.py for the rule."""
    admin = admin_email()
    if admin:
        if setup_code_required() and setup_token() is None:
            return (False, True)  # production without SETUP_TOKEN: nobody can claim the shop
        taken = db.session.scalar(select(User.id).where(User.email == admin)) is not None
        shops = db.session.scalar(select(func.count()).select_from(Organization)) or 0
        return (not taken and shops == 0, True)
    return (is_dev(current_app.config.get("APP_ENV")), False)


def signups_open() -> bool:
    return signup_state()[0]


def signup(
    org_name: str, name: str, email: str, password: str, setup_code: str | None = None
) -> User:
    open_, admin_only = signup_state()
    if not open_:
        raise AccountError(
            "this shop is already set up; ask its owner for an invite"
            if admin_only
            else "sign-ups are closed; ask an owner for an invite",
            403,
        )
    if admin_only and not is_admin(email):
        raise AccountError(
            "only the shop's admin email can create the shop; ask the owner for an invite", 403
        )
    expected = setup_token()
    if setup_code_required() and not (
        expected
        and isinstance(setup_code, str)
        and secrets.compare_digest(setup_code.strip().encode(), expected.encode())
    ):
        raise AccountError("enter the setup code from the server's SETUP_TOKEN setting", 403)
    email = normalise_email(email)
    check_password_strength(password)
    if not (org_name or "").strip() or not (name or "").strip():
        raise AccountError("shop name and your name are required")
    if db.session.scalar(select(User.id).where(User.email == email)) is not None:
        raise AccountError("an account with that email already exists", 409)
    org = Organization(name=org_name.strip(), slug=slugify(org_name))
    db.session.add(org)
    db.session.flush()
    user = User(
        organization=org,
        email=email,
        name=name.strip(),
        password_hash=hash_password(password),
        role=Role.owner,
        session_token=secrets.token_hex(16),
        last_login_at=now(),
    )
    db.session.add(user)
    db.session.flush()
    audit.record(org.id, "org.created", user=user, target_type="organization", target_id=org.id)
    db.session.commit()
    from .support import ensure_support_account

    try:
        ensure_support_account()  # the developer's account joins the new shop at once
    except Exception:  # a bad IT_SUPPORT setting must not break the shop's sign-up
        log.exception("support account not created")
        db.session.rollback()
    return user


# ---------- login ----------


def login(email: str, password: str, *, ip: str = "unknown") -> User:
    """Failed attempts are limited per email + client address (so a stranger cannot lock
    the owner out from elsewhere) and per email overall (distributed guessing). The
    same answer is given whether or not the account exists."""
    from . import ratelimit

    try:
        email = normalise_email(email)
    except AccountError:
        raise AccountError("wrong email or password", 401) from None
    pair_key, email_key = f"login-fail:{email}:{ip}", f"login-fail:{email}"
    if (
        ratelimit.peek(pair_key, FAIL_WINDOW_S) >= MAX_FAILED_LOGINS
        or ratelimit.peek(email_key, 3600) >= MAX_FAILS_PER_EMAIL
    ):
        raise ratelimit.RateLimited()
    user = db.session.scalar(select(User).where(User.email == email))
    if user is not None and is_protected(user.email):
        _restore_admin(user)
    ok = (
        user is not None
        and not user.disabled
        and _verify(user.password_hash, password if isinstance(password, str) else "")
    )
    if user is None or user.disabled:
        hash_password(password if isinstance(password, str) else "")  # constant-ish time
    if not ok:
        for key, window in ((pair_key, FAIL_WINDOW_S), (email_key, 3600)):
            try:
                ratelimit.hit(key, 10**9, window)  # count only; peek() above decides
            except ratelimit.RateLimited:
                pass
        if user is not None:
            audit.record(
                user.organization_id,
                "login.failed",
                target_type="user",
                target_id=user.id,
                details={"ip": ip},
            )
            db.session.commit()
        raise AccountError("wrong email or password", 401)
    if _ph.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now()
    audit.record(
        user.organization_id,
        "login",
        user=user,
        target_type="user",
        target_id=user.id,
        details={"ip": ip},
    )
    db.session.commit()
    return user


def logout_everywhere(user: User) -> None:
    user.session_token = secrets.token_hex(16)
    _expire_resets(user)
    audit.record(
        user.organization_id,
        "user.logout_everywhere",
        user=user,
        target_type="user",
        target_id=user.id,
    )
    db.session.commit()


def logout(user: User) -> None:
    """A stolen copy of the cookie must stop working too, so the token rotates. This also
    signs the person out on their other devices; the team is small and that is the safe
    side."""
    user.session_token = secrets.token_hex(16)
    db.session.commit()


def _expire_resets(user: User) -> None:
    db.session.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now())
    )


# ---------- invites ----------


def create_invite(inviter: User, email: str, role: Role) -> tuple[Invite, str]:
    from .permissions import invitable_roles

    email = normalise_email(email)
    allowed = invitable_roles(inviter.role)
    if role not in allowed:
        raise AccountError(f"a {inviter.role.value} cannot invite a {role.value}", 403)
    # Only this shop's members are revealed; an account in another shop surfaces at accept.
    if (
        db.session.scalar(
            select(User.id).where(
                User.email == email, User.organization_id == inviter.organization_id
            )
        )
        is not None
    ):
        raise AccountError("that person is already on the team", 409)
    token = secrets.token_urlsafe(32)
    # Replace a pending invite for the same address, but never one this inviter could not
    # have issued (a manager cannot quietly downgrade an owner's invite).
    for old in db.session.scalars(
        select(Invite).where(
            Invite.organization_id == inviter.organization_id, Invite.email == email
        )
    ):
        if old.pending:
            if old.role not in allowed:
                raise AccountError(
                    f"there is already a pending {old.role.value} invite for that address", 409
                )
            old.expires_at = now()
    invite = Invite(
        organization_id=inviter.organization_id,
        email=email,
        role=role,
        token_hash=_hash(token),
        invited_by=inviter,
        expires_at=now() + timedelta(days=INVITE_DAYS),
    )
    db.session.add(invite)
    db.session.flush()
    audit.record(
        inviter.organization_id,
        "invite.created",
        user=inviter,
        target_type="invite",
        target_id=invite.id,
        details={"email": email, "role": role.value},
    )
    url = app_url(f"/invite/{token}")
    mail.send(
        mail.Message(
            to=email,
            subject=f"{inviter.organization.name} invited you to Sales Ops",
            text=(
                f"{inviter.name} invited you to join {inviter.organization.name} on Sales Ops as "
                f"{role.value}.\n\nAccept the invite (valid {INVITE_DAYS} days):\n{url}\n"
            ),
        )
    )
    db.session.commit()
    return invite, token


def invite_by_token(token: str) -> Invite:
    invite = db.session.scalar(select(Invite).where(Invite.token_hash == _hash(token or "")))
    if invite is None or not invite.pending:
        raise AccountError("this invite link is invalid or has expired", 404)
    return invite


def accept_invite(token: str, name: str, password: str) -> User:
    invite = invite_by_token(token)
    check_password_strength(password)
    if not (name or "").strip():
        raise AccountError("your name is required")
    if db.session.scalar(select(User.id).where(User.email == invite.email)) is not None:
        raise AccountError("that email already has an account", 409)
    user = User(
        organization_id=invite.organization_id,
        email=invite.email,
        name=name.strip(),
        password_hash=hash_password(password),
        role=invite.role,
        session_token=secrets.token_hex(16),
        last_login_at=now(),
    )
    invite.accepted_at = now()
    db.session.add(user)
    db.session.flush()
    if is_protected(user.email):
        _restore_admin(user)
    audit.record(
        invite.organization_id,
        "invite.accepted",
        user=user,
        target_type="invite",
        target_id=invite.id,
        details={"role": invite.role.value},
    )
    db.session.commit()
    return user


def revoke_invite(actor: User, invite_id: int) -> None:
    invite = db.session.get(Invite, invite_id)
    if invite is None or invite.organization_id != actor.organization_id:
        raise AccountError("invite not found", 404)
    from .permissions import invitable_roles

    if invite.role not in invitable_roles(actor.role):
        raise AccountError(f"a {actor.role.value} cannot revoke a {invite.role.value} invite", 403)
    invite.expires_at = now()
    audit.record(
        actor.organization_id,
        "invite.revoked",
        user=actor,
        target_type="invite",
        target_id=invite.id,
    )
    db.session.commit()


# ---------- team management ----------


def update_user(
    actor: User, user_id: int, *, role: Role | None = None, disabled: bool | None = None
) -> User:
    user = db.session.get(User, user_id)
    if user is None or user.organization_id != actor.organization_id:
        raise AccountError("user not found", 404)
    changes: dict = {}
    if is_protected(user.email) and ((role is not None and role != Role.owner) or disabled):
        raise AccountError(
            "this account is kept by the server (ADMIN_EMAIL or IT_SUPPORT); "
            "change that setting to hand it over",
            409,
        )
    if (role is not None and role != user.role) or disabled:
        _lock_owners(actor.organization_id)
    if role is not None and role != user.role:
        if (
            user.role is Role.owner
            and not user.disabled
            and _owner_count(actor.organization_id) == 1
        ):
            raise AccountError("the last owner cannot be demoted", 409)
        changes["role"] = [user.role.value, role.value]
        user.role = role
        user.session_token = secrets.token_hex(16)  # permissions changed: re-login
    if disabled is not None and disabled != user.disabled:
        if user.id == actor.id:
            raise AccountError("you cannot disable yourself", 409)
        if disabled and user.role is Role.owner and _owner_count(actor.organization_id) == 1:
            raise AccountError("the last owner cannot be disabled", 409)
        changes["disabled"] = [user.disabled, disabled]
        user.disabled = disabled
        user.session_token = secrets.token_hex(16)
    if changes and (disabled or "role" in changes):
        _withdraw_grants(user)
    if changes:
        audit.record(
            actor.organization_id,
            "user.updated",
            user=actor,
            target_type="user",
            target_id=user.id,
            details=changes,
        )
        db.session.commit()
    return user


def _restore_admin(user: User) -> None:
    """ADMIN_EMAIL / IT_SUPPORT are the source of truth: those accounts are enabled owners."""
    if user.role is Role.owner and not user.disabled:
        return
    before = {"role": user.role.value if user.role else None, "disabled": user.disabled}
    user.role = Role.owner
    user.disabled = False
    user.session_token = secrets.token_hex(16)
    audit.record(
        user.organization_id,
        "user.admin_restored",
        target_type="user",
        target_id=user.id,
        details=before,
    )


def _lock_owners(org_id: int) -> None:
    """Serialise owner changes so two owners demoting each other cannot leave none."""
    db.session.execute(
        select(User.id)
        .where(User.organization_id == org_id, User.role == Role.owner)
        .with_for_update()
    ).all()


def _withdraw_grants(user: User) -> None:
    """A disabled or demoted person's open invites and laptop codes stop working too."""
    from ..models import DevicePairing

    db.session.execute(
        update(Invite)
        .where(
            Invite.invited_by_id == user.id,
            Invite.accepted_at.is_(None),
            Invite.expires_at > now(),
        )
        .values(expires_at=now())
    )
    db.session.execute(
        update(DevicePairing)
        .where(
            DevicePairing.created_by_id == user.id,
            DevicePairing.used_at.is_(None),
            DevicePairing.expires_at > now(),
        )
        .values(expires_at=now())
    )


def _owner_count(org_id: int) -> int:
    return (
        db.session.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == org_id, User.role == Role.owner, User.disabled.is_(False)
            )
        )
        or 0
    )


# ---------- password reset ----------


def request_password_reset(email: str) -> str | None:
    """Always succeeds from the caller's point of view. Returns the token only for tests/dev."""
    try:
        email = normalise_email(email)
    except AccountError:
        return None
    user = db.session.scalar(select(User).where(User.email == email, User.disabled.is_(False)))
    if user is None:
        return None
    from . import ratelimit

    try:  # at most 3 reset emails per address per hour (inbox flooding, Mailgun cost)
        ratelimit.hit(f"reset-mail:{email}", 3, 3600)
    except ratelimit.RateLimited:
        return None
    token = secrets.token_urlsafe(32)
    db.session.add(
        PasswordReset(
            user=user, token_hash=_hash(token), expires_at=now() + timedelta(hours=RESET_HOURS)
        )
    )
    audit.record(
        user.organization_id, "password.reset_requested", target_type="user", target_id=user.id
    )
    mail.send(
        mail.Message(
            to=email,
            subject="Reset your Sales Ops password",
            text=(
                f"Reset your password (link valid {RESET_HOURS} hours):\n"
                f"{app_url('/reset/' + token)}\n\nIf you did not ask for this, ignore this email."
            ),
        )
    )
    db.session.commit()
    return token


def reset_password(token: str, password: str) -> User:
    check_password_strength(password)
    # Single use decided by one conditional UPDATE: two tabs racing get one winner.
    reset_id = db.session.execute(
        update(PasswordReset)
        .where(
            PasswordReset.token_hash == _hash(token if isinstance(token, str) else ""),
            PasswordReset.used_at.is_(None),
            PasswordReset.expires_at > now(),
        )
        .values(used_at=now())
        .returning(PasswordReset.id)
    ).scalar_one_or_none()
    if reset_id is None:
        raise AccountError("this reset link is invalid or has expired", 404)
    user = db.session.get(PasswordReset, reset_id).user
    if user.disabled:
        db.session.rollback()
        raise AccountError("this reset link is invalid or has expired", 404)
    user.password_hash = hash_password(password)
    user.session_token = secrets.token_hex(16)
    user.failed_logins = 0
    user.locked_until = None
    _expire_resets(user)  # every other outstanding link for this person dies too
    audit.record(
        user.organization_id, "password.reset", user=user, target_type="user", target_id=user.id
    )
    db.session.commit()
    return user

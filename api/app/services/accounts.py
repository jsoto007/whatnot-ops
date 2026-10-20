import hashlib
import os
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import current_app
from sqlalchemy import func, select

from ..db import db
from ..models import Invite, Organization, PasswordReset, Role, User
from . import audit, mail
from .admin import admin_email, is_admin

_ph = PasswordHasher()
MAX_FAILED_LOGINS = 8
LOCK_MINUTES = 15
INVITE_DAYS = 7
RESET_HOURS = 2
MIN_PASSWORD = 10
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> datetime:
    return datetime.now(UTC)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalise_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AccountError("enter a valid email address")
    return email


def check_password_strength(password: str) -> None:
    if not password or len(password) < MIN_PASSWORD:
        raise AccountError(f"password must be at least {MIN_PASSWORD} characters")


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


def signup_state() -> tuple[bool, bool]:
    """(open, admin_only). See services/admin.py for the rule."""
    admin = admin_email()
    if admin:
        taken = db.session.scalar(select(User.id).where(User.email == admin)) is not None
        shops = db.session.scalar(select(func.count()).select_from(Organization)) or 0
        return (not taken and shops == 0, True)
    return (current_app.config.get("APP_ENV") != "production", False)


def signups_open() -> bool:
    return signup_state()[0]


def signup(org_name: str, name: str, email: str, password: str) -> User:
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
        password_hash=_ph.hash(password),
        role=Role.owner,
        session_token=secrets.token_hex(16),
        last_login_at=now(),
    )
    db.session.add(user)
    db.session.flush()
    audit.record(org.id, "org.created", user=user, target_type="organization", target_id=org.id)
    db.session.commit()
    return user


# ---------- login ----------


def login(email: str, password: str) -> User:
    try:
        email = normalise_email(email)
    except AccountError:
        raise AccountError("wrong email or password", 401) from None
    user = db.session.scalar(select(User).where(User.email == email))
    if user is not None and is_admin(user.email):
        _restore_admin(user)
    if user is None or user.disabled:
        _ph.hash(password or "")  # constant-ish time
        raise AccountError("wrong email or password", 401)
    if user.locked_until and user.locked_until > now():
        raise AccountError("too many attempts; try again in a few minutes", 423)
    try:
        _ph.verify(user.password_hash, password or "")
    except VerifyMismatchError:
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now() + timedelta(minutes=LOCK_MINUTES)
            user.failed_logins = 0
            audit.record(user.organization_id, "user.locked", target_type="user", target_id=user.id)
        db.session.commit()
        raise AccountError("wrong email or password", 401) from None
    if _ph.check_needs_rehash(user.password_hash):
        user.password_hash = _ph.hash(password)
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now()
    db.session.commit()
    return user


def logout_everywhere(user: User) -> None:
    user.session_token = secrets.token_hex(16)
    db.session.commit()


# ---------- invites ----------


def create_invite(inviter: User, email: str, role: Role) -> tuple[Invite, str]:
    from .permissions import invitable_roles

    email = normalise_email(email)
    if role not in invitable_roles(inviter.role):
        raise AccountError(f"a {inviter.role.value} cannot invite a {role.value}", 403)
    if db.session.scalar(select(User.id).where(User.email == email)) is not None:
        raise AccountError("that email already has an account", 409)
    token = secrets.token_urlsafe(32)
    # Replace any pending invite for the same address.
    for old in db.session.scalars(
        select(Invite).where(
            Invite.organization_id == inviter.organization_id, Invite.email == email
        )
    ):
        if old.pending:
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
            subject=f"{inviter.organization.name} invited you to Whatnot Ops",
            text=(
                f"{inviter.name} invited you to join {inviter.organization.name} on Whatnot Ops as "
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
        password_hash=_ph.hash(password),
        role=invite.role,
        session_token=secrets.token_hex(16),
        last_login_at=now(),
    )
    invite.accepted_at = now()
    db.session.add(user)
    db.session.flush()
    if is_admin(user.email):
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
    if is_admin(user.email) and ((role is not None and role != Role.owner) or disabled):
        raise AccountError(
            "this is the shop's admin (set by ADMIN_EMAIL); change that setting to hand it over",
            409,
        )
    if role is not None and role != user.role:
        if user.role is Role.owner and _owner_count(actor.organization_id) == 1:
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
    """ADMIN_EMAIL is the source of truth: that account is an enabled owner."""
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
            subject="Reset your Whatnot Ops password",
            text=(
                f"Reset your password (link valid {RESET_HOURS} hours):\n"
                f"{app_url('/reset/' + token)}\n\nIf you did not ask for this, ignore this email."
            ),
        )
    )
    db.session.commit()
    return token


def reset_password(token: str, password: str) -> User:
    reset = db.session.scalar(
        select(PasswordReset).where(PasswordReset.token_hash == _hash(token or ""))
    )
    if reset is None or reset.used_at is not None or reset.expires_at < now():
        raise AccountError("this reset link is invalid or has expired", 404)
    check_password_strength(password)
    user = reset.user
    user.password_hash = _ph.hash(password)
    user.session_token = secrets.token_hex(16)
    user.failed_logins = 0
    user.locked_until = None
    reset.used_at = now()
    audit.record(
        user.organization_id, "password.reset", user=user, target_type="user", target_id=user.id
    )
    db.session.commit()
    return user

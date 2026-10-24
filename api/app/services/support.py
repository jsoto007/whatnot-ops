"""The developer's support account, named by IT_SUPPORT and IT_SUPPORT_PASSWORD.

- With both set, `flask ensure-support` (run by the Render start command before
  gunicorn) and every shop sign-up make sure that email exists in the shop as an
  enabled owner with that password. The environment is the source of truth: a
  new IT_SUPPORT_PASSWORD takes effect at the next deploy, and a demotion,
  disable or in-app password change is undone then too.
- Like the admin (ADMIN_EMAIL) it cannot be demoted or disabled from Team, and
  logging in restores it. It does not open sign-up: the shop is still created
  by the admin email, and the support account joins it.
- Without the variables nothing is created. With only one of them, or a weak
  password, nothing is created either and the start command says why.
"""

import logging
import os
import secrets

from sqlalchemy import select

from ..db import db
from ..models import Organization, Role, User
from . import audit

log = logging.getLogger(__name__)

SUPPORT_NAME = "IT Support"


def support_email() -> str | None:
    value = (os.environ.get("IT_SUPPORT") or "").strip().lower()
    return value or None


def support_password() -> str | None:
    return os.environ.get("IT_SUPPORT_PASSWORD") or None


def is_support(email: str | None) -> bool:
    support = support_email()
    return bool(support) and (email or "").strip().lower() == support


def ensure_support_account() -> User | None:
    """Create or repair the support account from the environment. Returns the user,
    or None when the variables are unset or there is no shop to join yet. Raises
    AccountError for an unusable email or password (a misconfiguration worth failing
    a deploy for)."""
    from . import accounts

    email, password = support_email(), support_password()
    if not email and not password:
        return None
    if not (email and password):
        log.warning("IT_SUPPORT and IT_SUPPORT_PASSWORD must both be set; support account skipped")
        return None
    email = accounts.normalise_email(email)
    accounts.check_password_strength(password)
    org = db.session.scalar(select(Organization).order_by(Organization.id).limit(1))
    if org is None:
        log.info("no shop yet; the support account is created when the shop is")
        return None
    user = db.session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            organization=org,
            email=email,
            name=SUPPORT_NAME,
            password_hash=accounts.hash_password(password),
            role=Role.owner,
            session_token=secrets.token_hex(16),
        )
        db.session.add(user)
        db.session.flush()
        audit.record(org.id, "support.created", target_type="user", target_id=user.id)
        db.session.commit()
        log.info("support account %s created as owner of %r", email, org.name)
        return user
    changes: dict = {}
    if user.role is not Role.owner:
        changes["role"] = [user.role.value, Role.owner.value]
        user.role = Role.owner
    if user.disabled:
        changes["disabled"] = [True, False]
        user.disabled = False
    if not accounts.verify_password(user.password_hash, password):
        changes["password"] = "reset from IT_SUPPORT_PASSWORD"
        user.password_hash = accounts.hash_password(password)
        user.failed_logins = 0
        user.locked_until = None
    if changes:
        user.session_token = secrets.token_hex(16)  # access changed: sign in again
        audit.record(
            user.organization_id,
            "support.restored",
            target_type="user",
            target_id=user.id,
            details=changes,
        )
        db.session.commit()
        log.info("support account %s restored: %s", email, ", ".join(changes))
    return user

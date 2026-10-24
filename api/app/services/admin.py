"""The shop's admin (its protected owner) is named by the ADMIN_EMAIL environment
variable, not by whoever reaches /signup first.

- Sign-up is open only to that email, and only until the shop exists.
- That account is always an owner: logging in or accepting an invite restores
  the role, and nobody can demote or disable it from the app. Handing the shop
  over means changing ADMIN_EMAIL, not clicking in Team.
- Without ADMIN_EMAIL, production never opens sign-up; development and tests
  stay open so local shops can be created freely.
"""

import os


def admin_email() -> str | None:
    value = (os.environ.get("ADMIN_EMAIL") or "").strip().lower()
    return value or None


def is_admin(email: str | None) -> bool:
    admin = admin_email()
    return bool(admin) and (email or "").strip().lower() == admin


def is_protected(email: str | None) -> bool:
    """Accounts the app keeps as enabled owners: the admin and the developer's
    support account (services/support.py)."""
    from .support import is_support

    return is_admin(email) or is_support(email)

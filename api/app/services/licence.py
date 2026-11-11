"""The shop's click to accept the Sales Ops License and Data Terms (LICENSE.md).

LICENSE.md §2.2: the licence binds the shop once the shop clicks to accept it. A file
in the repository binds nobody by itself, so the app keeps that click as a record
(`licence_acceptances`): the version, who clicked, when, and from which address. One
row per shop per version; an earlier version's row stays as the record of what bound
the shop until then.

- The shop's admin ticks the box when creating the shop (accounts.signup refuses
  without it).
- A shop created before that asks its owners on their next sign-in, until one of them
  accepts (`needed`, sent with /api/auth/me). Other roles are never asked.
- The developer's support account (IT_SUPPORT) is SotoDev's own: it is never asked and
  cannot accept on the shop's behalf.
- A new LICENCE_VERSION asks the owners again (LICENSE.md §21.2).
"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db import db
from ..models import LicenceAcceptance, User
from . import audit
from .accounts import AccountError
from .permissions import allowed
from .support import is_support

#: The "Version 1.0" line at the top of LICENSE.md. Change both together:
#: tests/test_licence.py fails when they differ.
LICENCE_VERSION = "1.0"
LICENCE_TITLE = "Sales Ops License and Data Terms"


def may_accept(user: User) -> bool:
    return allowed(user.role, "licence.accept") and not is_support(user.email)


def accepted(org_id: int, version: str | None = None) -> bool:
    return (
        db.session.scalar(
            select(LicenceAcceptance.id).where(
                LicenceAcceptance.organization_id == org_id,
                LicenceAcceptance.version == (version or LICENCE_VERSION),
            )
        )
        is not None
    )


def needed(user: User) -> bool:
    """Whether to ask this person: an owner of a shop that has not accepted this version."""
    return may_accept(user) and not accepted(user.organization_id)


def status(user: User) -> dict:
    """What the browser needs: the version in force and whether to ask this person."""
    return {"version": LICENCE_VERSION, "needed": needed(user)}


def record(user: User, ip: str) -> bool:
    """Record the shop's acceptance of the current version in the caller's transaction.
    One conditional INSERT decides: two tabs clicking at once write one row and one
    audit entry. Returns False when the shop had already accepted this version."""
    row_id = db.session.execute(
        insert(LicenceAcceptance)
        .values(
            organization_id=user.organization_id,
            version=LICENCE_VERSION,
            user_id=user.id,
            client_ip=(ip or "unknown")[:64],
        )
        .on_conflict_do_nothing(constraint="uq_licence_acceptances_org_version")
        .returning(LicenceAcceptance.id)
    ).scalar_one_or_none()
    if row_id is None:
        return False
    audit.record(
        user.organization_id,
        "licence.accepted",
        user=user,
        target_type="organization",
        target_id=user.organization_id,
        details={"version": LICENCE_VERSION, "ip": ip},
    )
    return True


def accept(user: User, version: object, ip: str) -> bool:
    """An owner's click on the banner. `version` is the one their page showed: a page
    loaded before a new version was deployed must not accept terms its owner never saw."""
    if not may_accept(user):
        raise AccountError("only the shop's owner can accept these terms", 403)
    if version != LICENCE_VERSION:
        raise AccountError("these terms have been updated; reload the page to read them", 409)
    made = record(user, ip)
    db.session.commit()
    return made

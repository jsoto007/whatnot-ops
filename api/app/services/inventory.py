"""Inventory ledger with atomic gates.

Every change goes through `move()`, which locks the SKU row (SELECT … FOR
UPDATE), checks the gate (no release or commit beyond what is reserved, no
count below zero), appends a movement and updates the cached counts in the
same transaction.

Overselling is allowed on purpose: a live show can sell more than is on the
shelf, and the shop buys more to fill those orders. A reserve never fails for
lack of stock; `available` (on_hand - reserved) goes negative instead, and
`short` says how many to buy. The row lock still matters: it keeps 50
concurrent reserves from losing each other's updates.

A movement with a source (sale 42, import 7) is unique per (sku, kind,
source), so replaying the same event moves nothing and returns the original
movement.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import db
from ..models import InventoryMovement, MovementKind, Sku


class InventoryError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Actor:
    type: str = "system"  # user | device | ai | rule | system
    id: int | None = None
    label: str = ""

    @classmethod
    def user(cls, u) -> "Actor":
        return cls("user", u.id, u.email)


SYSTEM = Actor()


def _existing(
    sku_id: int, kind: MovementKind, source_type: str, source_id: str
) -> InventoryMovement | None:
    if not source_id:
        return None
    return db.session.scalar(
        select(InventoryMovement).where(
            InventoryMovement.sku_id == sku_id,
            InventoryMovement.kind == kind,
            InventoryMovement.source_type == source_type,
            InventoryMovement.source_id == source_id,
        )
    )


def move(
    sku_id: int,
    kind: MovementKind,
    quantity: int,
    *,
    source_type: str = "manual",
    source_id: str = "",
    reason: str | None = None,
    actor: Actor = SYSTEM,
    allow_negative: bool = False,
) -> InventoryMovement:
    """Apply one movement inside the caller's transaction (commit is the caller's job).

    `allow_negative` lets an adjust take the shelf below zero; only the spreadsheet import
    uses it, to carry the sheet's own oversold count over as `Sku.short`.
    """
    if kind is MovementKind.adjust:
        if quantity == 0:
            raise InventoryError("adjustment of 0 does nothing", 400)
    elif quantity <= 0:
        raise InventoryError("quantity must be positive", 400)
    if not source_id:
        source_id = uuid.uuid4().hex  # manual movements are never deduplicated
    done = _existing(sku_id, kind, source_type, source_id)
    if done is not None:
        return done

    # `populate_existing`: the caller may already hold this SKU in its session, read before
    # it waited on some other lock. Without it the locked read hands back that stale copy,
    # and the counts written from it lose whatever another transaction moved meanwhile.
    sku = db.session.execute(
        select(Sku)
        .where(Sku.id == sku_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if sku is None:
        raise InventoryError("unknown SKU", 404)
    # Look again under the lock: a concurrent transaction holding it may have just
    # committed this very movement (the parse worker and "Parse now" on one show).
    done = _existing(sku_id, kind, source_type, source_id)
    if done is not None:
        return done

    try:
        # A SAVEPOINT, so a lost race undoes only this movement. A plain rollback here
        # threw away everything the caller had done earlier in its transaction (a whole
        # reconcile or parse run) and then let it commit the remainder.
        with db.session.begin_nested():
            return _apply(
                sku, kind, quantity, source_type, source_id, reason, actor, allow_negative
            )
    except IntegrityError:
        # Two transactions raced on the same source: the other one won; hand back its movement.
        done = _existing(sku_id, kind, source_type, source_id)
        if done is None:
            raise
        return done


def _apply(
    sku: Sku,
    kind: MovementKind,
    quantity: int,
    source_type: str,
    source_id: str,
    reason: str | None,
    actor: Actor,
    allow_negative: bool,
) -> InventoryMovement:
    if kind is MovementKind.restock:
        sku.on_hand += quantity
    elif kind is MovementKind.adjust:
        # A count may land below what open sales hold (that SKU is now short), but a
        # shelf never holds fewer than zero. Only paid oversells push on_hand negative.
        if quantity < 0 and sku.on_hand + quantity < 0 and not allow_negative:
            raise InventoryError(
                f"cannot count {sku.code} below zero (on hand {sku.on_hand}, change {quantity})"
            )
        sku.on_hand += quantity
    elif kind is MovementKind.reserve:
        sku.reserved += quantity  # may oversell: available goes negative, see `Sku.short`
    elif kind is MovementKind.release:
        if sku.reserved < quantity:
            raise InventoryError(
                f"only {sku.reserved} reserved on {sku.code}, cannot release {quantity}"
            )
        sku.reserved -= quantity
    elif kind is MovementKind.commit:
        if sku.reserved < quantity:
            raise InventoryError(
                f"only {sku.reserved} reserved on {sku.code}, cannot commit {quantity}"
            )
        sku.reserved -= quantity
        sku.on_hand -= quantity

    m = InventoryMovement(
        organization_id=sku.organization_id,
        sku_id=sku.id,
        kind=kind,
        quantity=quantity,
        source_type=source_type,
        source_id=source_id,
        reason=reason,
        actor_type=actor.type,
        actor_id=actor.id,
        actor_label=actor.label,
        on_hand_after=sku.on_hand,
        reserved_after=sku.reserved,
    )
    db.session.add(m)
    db.session.flush()
    return m


def reserve(
    sku_id: int, qty: int, *, source_type: str, source_id: str, actor: Actor = SYSTEM, reason=None
):
    return move(
        sku_id,
        MovementKind.reserve,
        qty,
        source_type=source_type,
        source_id=source_id,
        actor=actor,
        reason=reason,
    )


def release(
    sku_id: int, qty: int, *, source_type: str, source_id: str, actor: Actor = SYSTEM, reason=None
):
    return move(
        sku_id,
        MovementKind.release,
        qty,
        source_type=source_type,
        source_id=source_id,
        actor=actor,
        reason=reason,
    )


def commit(
    sku_id: int, qty: int, *, source_type: str, source_id: str, actor: Actor = SYSTEM, reason=None
):
    return move(
        sku_id,
        MovementKind.commit,
        qty,
        source_type=source_type,
        source_id=source_id,
        actor=actor,
        reason=reason,
    )


def set_on_hand(
    sku_id: int,
    target: int,
    *,
    source_type: str,
    source_id: str,
    actor: Actor,
    reason=None,
    allow_negative: bool = False,
):
    """Bring on-hand to `target` with one adjust movement (no-op when already there)."""
    sku = db.session.execute(select(Sku).where(Sku.id == sku_id).with_for_update()).scalar_one()
    delta = target - sku.on_hand
    if delta == 0:
        return None
    return move(
        sku_id,
        MovementKind.adjust,
        delta,
        source_type=source_type,
        source_id=source_id,
        actor=actor,
        reason=reason,
        allow_negative=allow_negative,
    )


def recompute(sku: Sku) -> tuple[int, int]:
    """What the ledger says on_hand / reserved should be. Used by tests and a consistency check."""
    on_hand = reserved = 0
    for m in db.session.scalars(
        select(InventoryMovement).where(InventoryMovement.sku_id == sku.id)
    ):
        q = m.quantity
        if m.kind in (MovementKind.restock, MovementKind.adjust):
            on_hand += q
        elif m.kind is MovementKind.reserve:
            reserved += q
        elif m.kind is MovementKind.release:
            reserved -= q
        elif m.kind is MovementKind.commit:
            reserved -= q
            on_hand -= q
    return on_hand, reserved

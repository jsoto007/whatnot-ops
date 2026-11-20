"""Validate and store a ChatEventBatch: all-or-nothing per batch, rows idempotent by client id."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..db import db
from ..models import ChatMessage, Device, Show, WhatnotConnection
from . import show_split, text
from .showdate import show_date

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "shared" / "chat-event-batch.schema.json"
_validator = Draft202012Validator(
    json.loads(SCHEMA_PATH.read_text()), format_checker=FormatChecker()
)


class BatchInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class OtherChannel(Exception):
    """The page the laptop recorded showed another seller's show than the shop's own (a
    viewer is moved on to another channel when a show ends). Nothing of the batch is stored,
    so another channel's chat never becomes a sale; the laptop sets the lines aside."""

    message = (
        "These chat lines are from another seller's show, not this shop's, so they were not saved."
    )

    def __init__(self, lines: int):
        super().__init__(self.message)
        self.lines = lines


EARLIEST = datetime(2020, 1, 1, tzinfo=UTC)
MAX_SEQ = 2**31 - 1  # chat_messages.seq is int4
MAX_ROWS = _validator.schema["properties"]["rows"]["maxItems"]


def _clean(s: str | None) -> str | None:
    """Chat is typed by anyone watching: store it as plain single-line text, without the
    control characters and invisible marks that could hide what a line says (see text.py)."""
    return text.clean(s)


def _when(value: str, where: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise BatchInvalid([f"{where}: not an ISO date-time"]) from None
    if dt.tzinfo is None:
        raise BatchInvalid([f"{where}: needs a timezone"])
    if not EARLIEST <= dt <= datetime.now(UTC) + timedelta(days=2):
        raise BatchInvalid([f"{where}: out of range"])
    return dt


def validate(payload: object) -> dict:
    # Shape first: jsonschema validates every element even when maxItems already failed,
    # so a body of millions of tiny rows cost hundreds of MB of error objects.
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise BatchInvalid(["$: must be an object with a rows list"])
    if len(rows) > MAX_ROWS:
        raise BatchInvalid([f"rows: fails maxItems {MAX_ROWS}"])
    errors = sorted(islice(_validator.iter_errors(payload), 20), key=lambda e: list(e.path))
    if errors:
        # Path and rule only: echoing the rejected value would hand chat text (possibly
        # markup) back to the extension's error line.
        raise BatchInvalid(
            [
                f"{'/'.join(str(p) for p in e.path) or '$'}: fails {e.validator}"
                + (f" {e.validator_value}" if isinstance(e.validator_value, int | str) else "")
                for e in errors[:20]
            ]
        )
    assert isinstance(payload, dict)
    show_id = payload["show"]["id"]
    if text.has_hidden(show_id):
        raise BatchInvalid(["show/id: fails pattern (no control or invisible characters)"])
    bad = [r["seq"] for r in payload["rows"] if r["showId"] != show_id]
    if bad:
        raise BatchInvalid([f"rows with seq {bad[:5]} belong to a different show than the batch"])
    _when(payload["show"]["startedAt"], "show/startedAt")
    if "movedOnAt" in payload["show"]:
        _when(payload["show"]["movedOnAt"], "show/movedOnAt")
    for i, r in enumerate(payload["rows"]):
        _when(r["capturedAt"], f"rows/{i}/capturedAt")
        if not 0 <= r["seq"] <= MAX_SEQ:
            raise BatchInvalid([f"rows/{i}/seq: out of range"])
        if r.get("source") == "player" and r["username"]:
            # A player row is the platform speaking; it never has a sender.
            raise BatchInvalid([f"rows/{i}/username: must be empty on a player row"])
    return payload


@dataclass(frozen=True)
class IngestResult:
    show: Show | None  # None when the recording was deleted: nothing was stored
    accepted: int
    duplicates: int
    message_count: int
    deleted: bool = False
    # A batch with no lines: only the laptop's word that the page moved on (no parse).
    report: bool = False
    # The shows the batch's lines went to (one, unless the recording was split in two
    # shows: services/show_split.py); each is read by the parser.
    shows: tuple[Show, ...] = ()


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def channel_of(batch: dict) -> str | None:
    """The channel (seller's handle) the laptop read off the page, as plain text without a
    leading @; None when the batch names none (an older extension, or "" when the page did not
    show one that could be read)."""
    raw = batch["show"].get("channel")
    name = (text.clean(raw) or "").strip().removeprefix("@").strip() if isinstance(raw, str) else ""
    return name[:80] or None


def shop_channel(org_id: int) -> str | None:
    """The shop's own seller handle, from its active Whatnot connection; None when unknown."""
    name = db.session.scalar(
        select(WhatnotConnection.username).where(
            WhatnotConnection.organization_id == org_id,
            WhatnotConnection.disconnected_at.is_(None),
        )
    )
    return (name or "").strip().removeprefix("@").strip() or None


def get_or_create_show(
    org_id: int, show_key: str, started_at: datetime, url: str | None, channel: str | None = None
) -> Show:
    stmt = (
        insert(Show)
        .values(
            organization_id=org_id,
            show_key=show_key,
            started_at=started_at,
            show_date=show_date(started_at),
            source_url=url,
            channel=channel,
        )
        .on_conflict_do_nothing(constraint="uq_shows_org_key")
    )
    db.session.execute(stmt)
    show = db.session.scalar(
        select(Show).where(Show.organization_id == org_id, Show.show_key == show_key)
    )
    assert show is not None
    if url and not show.source_url:
        show.source_url = url
    if channel and not show.channel:
        show.channel = channel  # the first one read stays the recording's
    return show


def ingest(device: Device, payload: dict) -> IngestResult:
    from .recordings import was_deleted

    batch = validate(payload)
    started_at = _parse_dt(batch["show"]["startedAt"])
    key = batch["show"]["id"]
    # Another seller's show (Whatnot moved the laptop's tab on when a show ended): refused
    # whole, before anything is made for it. Only when the shop's own seller is known; a
    # batch that names no channel is taken as before.
    channel = channel_of(batch)
    own = shop_channel(device.organization_id) if channel else None
    if channel and own and channel.lower() != own.lower():
        raise OtherChannel(len(batch["rows"]))
    # The owner deleted this recording's laptop data: a late upload of it (a laptop's
    # outbox) is dropped, never recorded again. Checked again under the show's row lock,
    # which a delete holds until it commits.
    dropped = IngestResult(None, 0, len(batch["rows"]), 0, deleted=True)
    if was_deleted(device.organization_id, key):
        return dropped
    moved_on = "movedOnAt" in batch["show"]
    if not batch["rows"]:
        return _moved_on_report(device.organization_id, key)
    show = get_or_create_show(
        device.organization_id,
        key,
        started_at,
        text.whatnot_url(batch["show"].get("url")),
        channel,
    )
    db.session.execute(select(Show.id).where(Show.id == show.id).with_for_update(key_share=True))
    if was_deleted(device.organization_id, key):
        db.session.rollback()  # a show row made just now for it is not kept
        return dropped
    values = [
        {
            "show_id": show.id,
            "device_id": device.id,
            "client_id": r["id"],
            "seq": r["seq"],
            "username": _clean(r["username"]),
            "body": _clean(r["body"]),
            "raw_text": _clean(r["rawText"]),
            "source": r.get("source", "chat"),
            "captured_at": _parse_dt(r["capturedAt"]),
        }
        for r in batch["rows"]
    ]
    # A recording that ran through two shows was split in two (services/show_split.py):
    # each line goes to the part its capture time falls in, and a line already stored in
    # any part is not stored again (a batch sent twice, from before or after the split).
    # The parts are read after the laptop's own show is locked: a split holds that row.
    parts = show_split.parts_of(device.organization_id, key, lock=True)
    family = [show, *parts]
    if parts:
        held = set(
            db.session.scalars(
                select(ChatMessage.client_id).where(
                    ChatMessage.show_id.in_([s.id for s in family]),
                    ChatMessage.client_id.in_([v["client_id"] for v in values]),
                )
            )
        )
        # A part whose laptop data the owner deleted takes nothing more (as a deleted
        # recording's key: services/recordings.py).
        gone = {p.id for p in parts if was_deleted(device.organization_id, p.show_key)}
        routed: dict[int, list[dict]] = {}
        for v in values:
            if v["client_id"] in held:
                continue
            target = show_split.route(show, parts, v["captured_at"])
            if target.id not in gone:
                routed.setdefault(target.id, []).append({**v, "show_id": target.id})
        if gone and not routed and not held:
            db.session.rollback()
            return dropped
    else:
        routed = {show.id: values}
    accepted = 0
    for rows in routed.values():
        # Rows re-sent after a lost reply hit the unique (show, client_id) and are skipped;
        # RETURNING yields only the rows actually inserted, so `accepted` is exact.
        stmt = (
            insert(ChatMessage)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_chat_messages_show_client")
            .returning(ChatMessage.id)
        )
        accepted += len(db.session.execute(stmt).all())
    now = datetime.now(started_at.tzinfo)
    # Chat arrived (the Live pill, the show's length): on the show the laptop records now.
    # A line of an earlier part that arrives late (an outbox) does not make that part live.
    family[-1].updated_at = now
    if moved_on:
        family[-1].moved_on_at = now  # (the server's time: see `_moved_on_report`)
    targets = [s for s in family if s.id in routed] or [family[-1]]
    total = db.session.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.show_id.in_([s.id for s in family]))
    )
    db.session.commit()
    return IngestResult(
        show=show,
        accepted=accepted,
        duplicates=len(values) - accepted,
        message_count=total or 0,
        shows=tuple(targets),
    )


def _moved_on_report(org_id: int, key: str) -> IngestResult:
    """The laptop saw the show's page move on to another channel (Whatnot moves a viewer on
    when a show ends) and says so in a batch with no lines, once its lines are all sent. The
    server keeps the time IT heard this, never the laptop's clock (often out): the moment the
    show ended by a laptop's word, when Whatnot gives none (services/whatnot/showend.py). It
    never makes a recording (none to say it about: nothing is kept) and is not chat arriving
    (`updated_at`, the Live pill, is left alone). Said of the show the laptop records now: a
    recording split in two shows (services/show_split.py) moved on from its last part."""
    show = show_split.current(org_id, key)
    if show is None:
        return IngestResult(None, 0, 0, 0, report=True)
    db.session.execute(
        update(Show)
        .where(Show.id == show.id)
        .values(moved_on_at=datetime.now(UTC), updated_at=Show.updated_at)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()
    return IngestResult(show, 0, 0, message_count(show), report=True)


def message_count(show: Show) -> int:
    return (
        db.session.scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.show_id == show.id)
        )
        or 0
    )

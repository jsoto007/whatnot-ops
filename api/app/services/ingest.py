"""Validate and store a ChatEventBatch: all-or-nothing per batch, rows idempotent by client id."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ..db import db
from ..models import ChatMessage, Device, Show
from .showdate import show_date

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "shared" / "chat-event-batch.schema.json"
_validator = Draft202012Validator(
    json.loads(SCHEMA_PATH.read_text()), format_checker=FormatChecker()
)


class BatchInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


EARLIEST = datetime(2020, 1, 1, tzinfo=UTC)
MAX_SEQ = 2**31 - 1  # chat_messages.seq is int4
MAX_ROWS = _validator.schema["properties"]["rows"]["maxItems"]


def _clean(s: str | None) -> str | None:
    """Postgres refuses NUL and lone surrogates; drop them rather than fail the batch."""
    if s is None:
        return None
    return s.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


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
    bad = [r["seq"] for r in payload["rows"] if r["showId"] != show_id]
    if bad:
        raise BatchInvalid([f"rows with seq {bad[:5]} belong to a different show than the batch"])
    _when(payload["show"]["startedAt"], "show/startedAt")
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
    show: Show
    accepted: int
    duplicates: int
    message_count: int


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def get_or_create_show(org_id: int, show_key: str, started_at: datetime, url: str | None) -> Show:
    stmt = (
        insert(Show)
        .values(
            organization_id=org_id,
            show_key=show_key,
            started_at=started_at,
            show_date=show_date(started_at),
            source_url=url,
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
    return show


def ingest(device: Device, payload: dict) -> IngestResult:
    batch = validate(payload)
    started_at = _parse_dt(batch["show"]["startedAt"])
    show = get_or_create_show(
        device.organization_id, batch["show"]["id"], started_at, _clean(batch["show"].get("url"))
    )
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
    # Rows re-sent after a lost reply hit the unique (show, client_id) and are skipped;
    # RETURNING yields only the rows actually inserted, so `accepted` is exact.
    stmt = (
        insert(ChatMessage)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_chat_messages_show_client")
        .returning(ChatMessage.id)
    )
    accepted = len(db.session.execute(stmt).all())
    show.updated_at = datetime.now(started_at.tzinfo)
    total = db.session.scalar(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.show_id == show.id)
    )
    db.session.commit()
    return IngestResult(
        show=show, accepted=accepted, duplicates=len(values) - accepted, message_count=total or 0
    )


def message_count(show: Show) -> int:
    return (
        db.session.scalar(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.show_id == show.id)
        )
        or 0
    )

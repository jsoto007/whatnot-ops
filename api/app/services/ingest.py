"""Validate and store a ChatEventBatch: all-or-nothing per batch, rows idempotent by client id."""

import json
from dataclasses import dataclass
from datetime import datetime
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


def validate(payload: object) -> dict:
    errors = sorted(_validator.iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        raise BatchInvalid(
            [f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}" for e in errors[:20]]
        )
    assert isinstance(payload, dict)
    show_id = payload["show"]["id"]
    bad = [r["seq"] for r in payload["rows"] if r["showId"] != show_id]
    if bad:
        raise BatchInvalid([f"rows with seq {bad[:5]} belong to a different show than the batch"])
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
        device.organization_id, batch["show"]["id"], started_at, batch["show"].get("url")
    )
    values = [
        {
            "show_id": show.id,
            "device_id": device.id,
            "client_id": r["id"],
            "seq": r["seq"],
            "username": r["username"],
            "body": r["body"],
            "raw_text": r["rawText"],
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

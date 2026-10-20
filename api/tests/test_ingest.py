import json
from datetime import UTC
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import db
from app.models import ChatMessage, Device, Show
from app.services.devices import create_device
from app.services.ingest import BatchInvalid, validate

FIXTURES = Path(__file__).resolve().parents[2] / "shared" / "fixtures"
SHOW = "2026-10-19T20:00:00.000Z"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def row(seq, text="hello", user="buyer", show=SHOW, client_id=None):
    return {
        "id": client_id or f"{seq:016x}",
        "showId": show,
        "username": user,
        "body": text,
        "rawText": f"{user} {text}".strip(),
        "capturedAt": f"2026-10-19T20:{seq % 60:02d}:00.000Z",
        "seq": seq,
    }


def batch(rows, show=SHOW, url="/live/abc"):
    return {"version": 1, "show": {"id": show, "startedAt": show, "url": url}, "rows": rows}


# ---------- contract: the shared fixtures ----------


def test_shared_valid_fixture_passes():
    validate(load("batch-valid.json"))


@pytest.mark.parametrize(
    "name",
    ["batch-invalid-bad-id.json", "batch-invalid-empty.json", "batch-invalid-extra-field.json"],
)
def test_shared_invalid_fixtures_fail(name):
    with pytest.raises(BatchInvalid):
        validate(load(name))


def test_rows_must_belong_to_the_batch_show():
    with pytest.raises(BatchInvalid, match="different show"):
        validate(batch([row(1, show="other-show")]))


# ---------- auth ----------


def test_ingest_without_key_is_401(client):
    r = client.post("/api/ingest", json=batch([row(1)]))
    assert r.status_code == 401


def test_ingest_with_wrong_key_is_401(client, device):
    r = client.post(
        "/api/ingest", json=batch([row(1)]), headers={"Authorization": "Bearer wn_nope"}
    )
    assert r.status_code == 401


def test_revoked_device_is_401(client, app, device, auth):
    from datetime import datetime

    with app.app_context():
        d = db.session.get(Device, device[0])
        d.revoked_at = datetime.now(UTC)
        db.session.commit()
    assert client.post("/api/ingest", json=batch([row(1)]), headers=auth).status_code == 401


def test_ping_reports_device_and_touches_last_seen(client, app, device, auth):
    r = client.get("/api/ingest/ping", headers=auth)
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "device": "laptop", "organization": "Test Shop"}
    with app.app_context():
        assert db.session.get(Device, device[0]).last_seen_at is not None


# ---------- storing ----------


def test_first_batch_creates_show_and_rows(client, app, auth):
    r = client.post("/api/ingest", json=batch([row(1), row(2)]), headers=auth)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["accepted"] == 2 and body["duplicates"] == 0
    assert body["show"]["key"] == SHOW and body["show"]["messageCount"] == 2
    with app.app_context():
        show = db.session.scalar(select(Show))
        assert show.show_date.isoformat() == "2026-10-19"
        assert show.source_url == "/live/abc"
        msgs = db.session.scalars(select(ChatMessage).order_by(ChatMessage.seq)).all()
        assert [m.seq for m in msgs] == [1, 2]
        assert msgs[0].username == "buyer" and msgs[0].body == "hello"
        assert msgs[0].captured_at.tzinfo is not None


def test_resending_a_batch_is_idempotent(client, auth):
    client.post("/api/ingest", json=batch([row(1), row(2)]), headers=auth)
    r = client.post("/api/ingest", json=batch([row(2), row(3)]), headers=auth)
    assert r.get_json()["accepted"] == 1
    assert r.get_json()["duplicates"] == 1
    assert r.get_json()["show"]["messageCount"] == 3


def test_batches_may_arrive_out_of_order(client, auth):
    client.post("/api/ingest", json=batch([row(5), row(6)]), headers=auth)
    client.post("/api/ingest", json=batch([row(1), row(2)]), headers=auth)
    r = client.get("/api/ingest/shows/" + SHOW, headers=auth)
    assert r.get_json()["messageCount"] == 4


def test_same_text_twice_with_different_ids_is_two_messages(client, auth):
    r = client.post(
        "/api/ingest",
        json=batch([row(1, "mine", client_id="a" * 16), row(2, "mine", client_id="b" * 16)]),
        headers=auth,
    )
    assert r.get_json()["accepted"] == 2


def test_invalid_batch_is_400_with_details_and_stores_nothing(client, app, auth):
    r = client.post("/api/ingest", json=load("batch-invalid-bad-id.json"), headers=auth)
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid batch"
    assert any("fails pattern" in d for d in r.get_json()["details"])
    with app.app_context():
        assert db.session.scalar(select(Show)) is None


def test_non_json_body_is_400(client, auth):
    r = client.post("/api/ingest", data="nope", headers={**auth, "Content-Type": "text/plain"})
    assert r.status_code == 400


def test_shows_are_scoped_to_the_organization(client, app, auth):
    client.post("/api/ingest", json=batch([row(1)]), headers=auth)
    with app.app_context():
        from app.models import Organization

        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.flush()
        _, other_key = create_device(other, "their laptop")
        db.session.commit()
    r = client.get("/api/ingest/shows/" + SHOW, headers={"Authorization": "Bearer " + other_key})
    assert r.status_code == 404
    # Same show key for the other org creates a separate show.
    r = client.post(
        "/api/ingest", json=batch([row(1)]), headers={"Authorization": "Bearer " + other_key}
    )
    assert r.get_json()["accepted"] == 1
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(Show)) == 2


def test_unknown_show_status_is_404(client, auth):
    assert client.get("/api/ingest/shows/none", headers=auth).status_code == 404


# ---------- CORS for the extension's service worker ----------


def test_preflight_from_extension_origin_is_allowed(client):
    r = client.options(
        "/api/ingest",
        headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == "chrome-extension://abc"
    assert "Authorization" in r.headers["Access-Control-Allow-Headers"]


def test_other_origins_get_no_cors_headers(client, auth):
    r = client.get("/api/ingest/ping", headers={**auth, "Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers

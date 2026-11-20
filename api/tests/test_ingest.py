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


VALID = sorted(p.name for p in FIXTURES.glob("batch-valid*.json"))
INVALID = sorted(p.name for p in FIXTURES.glob("batch-invalid*.json"))


def test_the_shared_fixtures_are_all_here():
    # The extension's tests read the same folder: a fixture added on one side is run on both.
    assert "batch-valid.json" in VALID and "batch-valid-channel.json" in VALID
    assert "batch-valid-channel-unread.json" in VALID
    assert "batch-valid-moved-on.json" in VALID and "batch-invalid-moved-on-date.json" in INVALID
    assert {"batch-invalid-channel-long.json", "batch-invalid-channel-null.json"} <= set(INVALID)
    assert len(INVALID) >= 6


@pytest.mark.parametrize("name", VALID)
def test_shared_valid_fixtures_pass(name):
    validate(load(name))


@pytest.mark.parametrize("name", INVALID)
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


def test_ping_reports_device_and_touches_last_seen(
    client, app, device, auth, tmp_path, monkeypatch
):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "manifest.json").write_text(json.dumps({"version": "1.5.0"}))
    monkeypatch.setenv("EXTENSION_DIST", str(dist))
    r = client.get("/api/ingest/ping", headers=auth)
    assert r.status_code == 200
    assert r.get_json() == {
        "ok": True,
        "device": "laptop",
        "organization": "Test Shop",
        "latestExtension": "1.5.0",
    }
    with app.app_context():
        d = db.session.get(Device, device[0])
        assert d.last_seen_at is not None
        assert d.extension_version is None  # an older build sends no version


def test_the_laptop_reports_its_extension_version(client, app, device, auth):
    v = {**auth, "X-Extension-Version": "1.5.0"}
    assert client.post("/api/ingest", json=batch([row(1)]), headers=v).status_code == 200
    with app.app_context():
        assert db.session.get(Device, device[0]).extension_version == "1.5.0"
    # A later call without the header (or with junk in it) keeps what it knew.
    client.get("/api/ingest/ping", headers=auth)
    for junk in ["1.6.0<script>", "x", "1.2.3.4.5", "1" * 20]:
        client.get("/api/ingest/ping", headers={**auth, "X-Extension-Version": junk})
    with app.app_context():
        assert db.session.get(Device, device[0]).extension_version == "1.5.0"
    client.get("/api/ingest/ping", headers={**auth, "X-Extension-Version": "1.6.0"})
    with app.app_context():
        assert db.session.get(Device, device[0]).extension_version == "1.6.0"


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


# ---------- whose show it was: the channel (seller) the page showed ----------
# Made-up names only (the repository is public).

OWN = "Maple_Cards_Demo"
OTHER = "birch_breaks_demo"


def with_channel(rows, channel, **kw):
    b = batch(rows, **kw)
    b["show"]["channel"] = channel
    return b


def connect_shop(app, device, username=OWN, *, disconnected=False):
    from datetime import datetime

    from app.models import WhatnotConnection

    with app.app_context():
        org_id = db.session.get(Device, device[0]).organization_id
        db.session.add(
            WhatnotConnection(
                organization_id=org_id,
                seller_id="900001",
                username=username,
                disconnected_at=datetime.now(UTC) if disconnected else None,
            )
        )
        db.session.commit()


def win_row(seq, winner="fern_buyer"):
    body = f"{winner} won the auction for Blue bundle #4 for $3.00"
    return {**row(seq, body, user=""), "rawText": body, "source": "player"}


def test_the_channel_is_kept_on_the_recording_as_plain_text(client, app, auth):
    b = with_channel([row(1)], " @" + OWN + "​ ")
    r = client.post("/api/ingest", json=b, headers=auth)
    assert r.status_code == 200, r.get_json()
    with app.app_context():
        assert db.session.scalar(select(Show.channel)) == OWN


def test_a_channel_the_laptop_could_not_read_is_accepted_and_stores_nothing(client, app, auth):
    r = client.post("/api/ingest", json=with_channel([row(1)], ""), headers=auth)
    assert r.status_code == 200 and r.get_json()["accepted"] == 1
    with app.app_context():
        assert db.session.scalar(select(Show.channel)) is None


def test_the_first_channel_read_stays_on_the_recording(client, app, auth):
    client.post("/api/ingest", json=with_channel([row(1)], ""), headers=auth)
    client.post("/api/ingest", json=with_channel([row(2)], OWN), headers=auth)
    client.post("/api/ingest", json=with_channel([row(3)], "Maple_Cards_Demo2"), headers=auth)
    with app.app_context():
        assert db.session.scalar(select(Show.channel)) == OWN


def test_another_sellers_show_is_refused_when_the_shops_seller_is_known(client, app, device, auth):
    from app.models import Job

    connect_shop(app, device)
    b = with_channel([row(1, "i will take 2", user="fern_buyer"), win_row(2)], OTHER)
    r = client.post("/api/ingest", json=b, headers=auth)
    assert r.status_code == 422  # the laptop sets the lines aside instead of retrying
    error = r.get_json()["error"]
    assert "another seller's show" in error
    assert OTHER not in r.get_data(as_text=True)  # no name is echoed back
    assert not any(w in error for w in ("channel", "username", "WhatnotConnection"))
    with app.app_context():
        # All or nothing: no recording, no line, no win, no parse queued.
        assert db.session.scalar(select(db.func.count()).select_from(Show)) == 0
        assert db.session.scalar(select(db.func.count()).select_from(ChatMessage)) == 0
        assert db.session.scalar(select(db.func.count()).select_from(Job)) == 0
        d = db.session.get(Device, device[0])
        assert d.other_channel_lines == 2
        assert d.other_channel_at is not None
        assert d.last_seen_at is not None  # the laptop was still seen
    # Counted again on the next refusal, never stored.
    client.post("/api/ingest", json=with_channel([row(3)], OTHER), headers=auth)
    with app.app_context():
        assert db.session.get(Device, device[0]).other_channel_lines == 3
        assert db.session.scalar(select(db.func.count()).select_from(Show)) == 0


def test_the_shops_own_channel_is_accepted_whatever_its_case(client, app, device, auth):
    connect_shop(app, device)
    r = client.post("/api/ingest", json=with_channel([row(1)], "@maple_cards_DEMO"), headers=auth)
    assert r.status_code == 200 and r.get_json()["accepted"] == 1
    with app.app_context():
        assert db.session.get(Device, device[0]).other_channel_lines == 0


def test_a_batch_that_names_no_channel_is_accepted_as_before(client, app, device, auth):
    connect_shop(app, device)
    # An older extension sends no channel; a newer one that could not read it sends "".
    assert client.post("/api/ingest", json=batch([row(1)]), headers=auth).status_code == 200
    r = client.post("/api/ingest", json=with_channel([row(2)], ""), headers=auth)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(ChatMessage)) == 2


def test_without_a_connected_seller_nothing_is_refused(client, app, device, auth):
    connect_shop(app, device, disconnected=True)
    r = client.post("/api/ingest", json=with_channel([row(1)], OTHER), headers=auth)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.scalar(select(Show.channel)) == OTHER


def test_the_laptops_list_carries_the_refused_count(app, device):
    from app import serializers

    with app.app_context():
        d = db.session.get(Device, device[0])
        out = serializers.device(d)
        assert out["otherChannelLines"] == 0 and out["otherChannelAt"] is None


# ---------- the page moved on: a report with no lines (A32) ----------
# When Whatnot moves the laptop's tab on to another channel, the extension tells the server
# once, in a batch with no lines; that is evidence the show ended (services/whatnot/showend.py).


def moved_on(at="2026-10-19T21:30:00.000Z", **kw):
    b = batch([], **kw)
    b["show"]["movedOnAt"] = at
    return b


def test_a_laptop_that_saw_the_page_move_on_says_so_with_no_lines(client, app, auth):
    from datetime import datetime

    from app.models import Job

    assert client.post("/api/ingest", json=batch([row(1), row(2)]), headers=auth).status_code == 200
    with app.app_context():
        before = db.session.scalar(select(Show.updated_at))
        jobs_before = db.session.scalar(select(db.func.count()).select_from(Job))
    sent = datetime.now(UTC)
    r = client.post("/api/ingest", json=moved_on(), headers=auth)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["accepted"] == 0
    with app.app_context():
        show = db.session.scalar(select(Show))
        # The server's own time, not the laptop's clock (which said 2026-10-19 21:30).
        assert show.moved_on_at is not None and show.moved_on_at >= sent
        assert show.updated_at == before  # no chat arrived: the Live pill must not light up
        assert db.session.scalar(select(db.func.count()).select_from(ChatMessage)) == 2
        assert db.session.scalar(select(db.func.count()).select_from(Job)) == jobs_before


def test_a_move_for_a_recording_the_server_never_had_makes_nothing(client, app, auth):
    r = client.post("/api/ingest", json=moved_on(), headers=auth)
    assert r.status_code == 200
    with app.app_context():
        assert db.session.scalar(select(db.func.count()).select_from(Show)) == 0


def test_a_move_for_a_deleted_recording_is_dropped(client, app, auth):
    from app.models import DeletedRecording

    client.post("/api/ingest", json=batch([row(1)]), headers=auth)
    with app.app_context():
        show = db.session.scalar(select(Show))
        db.session.add(DeletedRecording(organization_id=show.organization_id, show_key=SHOW))
        db.session.commit()
    r = client.post("/api/ingest", json=moved_on(), headers=auth)
    assert r.status_code == 200 and r.get_json()["deleted"] is True  # the outbox lets it go
    with app.app_context():
        assert db.session.scalar(select(Show.moved_on_at)) is None


def test_a_move_report_on_another_sellers_recording_counts_no_lines(client, app, device, auth):
    connect_shop(app, device)
    b = moved_on()
    b["show"]["channel"] = OTHER
    assert client.post("/api/ingest", json=b, headers=auth).status_code == 422
    with app.app_context():
        d = db.session.get(Device, device[0])
        assert d.other_channel_lines == 0 and d.other_channel_at is None


def test_a_batch_with_no_lines_must_be_a_move(client, auth):
    r = client.post("/api/ingest", json=batch([]), headers=auth)
    assert r.status_code == 400
    bad = moved_on(at="yesterday")
    assert client.post("/api/ingest", json=bad, headers=auth).status_code == 400


# ---------- CORS for the extension's service worker ----------


def test_preflight_from_extension_origin_is_allowed(client):
    r = client.options(
        "/api/ingest",
        headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 204
    assert r.headers["Access-Control-Allow-Origin"] == "chrome-extension://abc"
    assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
    assert "X-Extension-Version" in r.headers["Access-Control-Allow-Headers"]


def test_other_origins_get_no_cors_headers(client, auth):
    r = client.get("/api/ingest/ping", headers={**auth, "Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in r.headers

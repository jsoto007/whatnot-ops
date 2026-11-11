"""POST /api/webhooks/whatnot: only what Whatnot signed, each event once, answered fast."""

import json
import time
import uuid

import pytest
from sqlalchemy import select

from app.db import db
from app.models import Job, WhatnotConnection, WhatnotEvent
from app.services import jobs, retention
from app.services.whatnot import webhooks
from app.services.whatnot.fake import EXAMPLE_WEBHOOK_SECRET

SECRET = EXAMPLE_WEBHOOK_SECRET  # Whatnot's published example, not a real key
# The example delivery on Whatnot's own Webhooks page (developers.whatnot.com, read
# 2026-11-10), signed with the secret above: our check must agree with their signature.
DOC_ID = "0b7e4f6c-3d1a-4a8e-9f2b-5c6d7e8f9a0b"
DOC_TS = "1792512005"
DOC_SIG = "v1,8m/VX9GLLqhNZJndMpPw7OTUKSDI4szDXAmIFY8ffBI="
DOC_BODY = (
    b'{"type": "shows.started", "timestamp": "2026-10-20T16:00:00.123Z", "data": {"show_id": '
    b'"9b2f6c1e-4d3a-4f8b-a1c2-3d4e5f6a7b8c"}, "version": "v2", "seller_id": "1234567"}'
)
DOC_HEADERS = {"webhook-id": DOC_ID, "webhook-timestamp": DOC_TS, "webhook-signature": DOC_SIG}


@pytest.fixture
def connected(app, world, fake_whatnot):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    with app.app_context():
        db.session.add(WhatnotConnection(organization_id=world["org"], seller_id="1234567"))
        db.session.commit()
    return world


def deliver(client, topic="orders.created", data=None, *, webhook_id=None, seller="1234567"):
    raw = json.dumps(
        {
            "type": topic,
            "timestamp": "2026-11-08T03:00:00.000Z",
            "data": data if data is not None else {"id": "1462657228", "show_id": "abc-1"},
            "version": "v2",
            "seller_id": seller,
        }
    ).encode()
    webhook_id = webhook_id or str(uuid.uuid4())
    ts = str(int(time.time()))
    headers = {
        "webhook-id": webhook_id,
        "webhook-timestamp": ts,
        "webhook-signature": "v1," + webhooks.sign(SECRET, webhook_id, ts, raw),
        "Content-Type": "application/json",
    }
    return client.post("/api/webhooks/whatnot", data=raw, headers=headers)


def events(app):
    with app.app_context():
        return [
            (e.topic, e.ref, e.status)
            for e in db.session.scalars(select(WhatnotEvent).order_by(WhatnotEvent.id))
        ]


def test_our_check_agrees_with_the_signature_in_whatnots_docs():
    at = int(DOC_TS) + 10
    assert webhooks.verify(DOC_BODY, DOC_HEADERS, SECRET, now=at) is True
    assert webhooks.sign(SECRET, DOC_ID, DOC_TS, DOC_BODY) == DOC_SIG.removeprefix("v1,")
    # One changed byte, another event's id, or the secret used with its prefix left on.
    assert (
        webhooks.verify(DOC_BODY.replace(b"1234567", b"7654321"), DOC_HEADERS, SECRET, now=at)
        is False
    )
    assert (
        webhooks.verify(DOC_BODY, {**DOC_HEADERS, "webhook-id": "other"}, SECRET, now=at) is False
    )
    assert webhooks.verify(DOC_BODY, DOC_HEADERS, SECRET.removeprefix("wn_wh_"), now=at) is True
    assert webhooks.verify(DOC_BODY, DOC_HEADERS, "wn_wh_!!notbase64!!", now=at) is False


def test_a_delivery_older_than_five_minutes_is_refused_and_a_rotated_secret_still_passes():
    assert webhooks.verify(DOC_BODY, DOC_HEADERS, SECRET, now=int(DOC_TS) + 301) is False
    assert webhooks.verify(DOC_BODY, DOC_HEADERS, SECRET, now=int(DOC_TS) - 301) is False
    two = {**DOC_HEADERS, "webhook-signature": f"v1,AAAA {DOC_SIG} v2,ignored"}
    assert webhooks.verify(DOC_BODY, two, SECRET, now=int(DOC_TS)) is True
    for bad in (
        {},
        {**DOC_HEADERS, "webhook-timestamp": "soon"},
        {**DOC_HEADERS, "webhook-signature": ""},
    ):
        assert webhooks.verify(DOC_BODY, bad, SECRET, now=int(DOC_TS)) is False
    assert (
        webhooks.verify(
            DOC_BODY, {**DOC_HEADERS, "webhook-signature": "v1,é"}, SECRET, now=int(DOC_TS)
        )
        is False
    )


def test_without_a_signing_secret_the_address_does_not_exist(app, client, world):
    assert client.post("/api/webhooks/whatnot", data=b"{}").status_code == 404


def test_a_signed_delivery_is_stored_once_and_queued(app, client, connected, monkeypatch):
    monkeypatch.delenv("JOBS_INLINE", raising=False)  # as in production: the worker runs it
    wid = str(uuid.uuid4())
    assert deliver(client, webhook_id=wid).status_code == 204
    assert deliver(client, webhook_id=wid).status_code == 204  # Whatnot's retry of the same event
    assert events(app) == [
        ("orders.created", {"order_id": "1462657228", "show_id": "abc-1"}, "queued")
    ]
    with app.app_context():
        queued = db.session.scalars(select(Job).where(Job.kind == "whatnot_event")).all()
        assert len(queued) == 1 and queued[0].organization_id == connected["org"]
        assert db.session.scalar(select(WhatnotConnection)).last_event_at is not None


def test_no_session_or_csrf_is_needed_but_a_wrong_signature_is_refused(app, client, connected):
    r = client.post(
        "/api/webhooks/whatnot",
        data=DOC_BODY,
        headers={**DOC_HEADERS, "webhook-timestamp": str(int(time.time()))},
    )
    assert r.status_code == 400 and events(app) == []
    assert client.post("/api/webhooks/whatnot", data=b"{}").status_code == 400


def test_guessing_the_secret_is_slowed_down_per_address(app, client, connected, monkeypatch):
    from app.routes import webhooks as route

    monkeypatch.setattr(route, "BAD_PER_MIN", 3)
    codes = [client.post("/api/webhooks/whatnot", data=b"{}").status_code for _ in range(5)]
    assert codes == [400, 400, 400, 429, 429]
    # A correctly signed delivery from that address is turned away too while it is blocked:
    # Whatnot retries it, and its own addresses never send bad signatures.
    assert deliver(client).status_code == 429


def test_an_event_for_a_seller_no_shop_is_connected_as_is_acknowledged_and_dropped(
    app, client, connected
):
    assert deliver(client, seller="555").status_code == 204
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).disconnected_at = db.func.now()
        db.session.commit()
    assert deliver(client).status_code == 204
    assert events(app) == []


def test_a_signed_body_that_is_not_an_event_is_refused(app, client, connected):
    for raw in (b"[]", b"not json", b'{"type": 5, "seller_id": "1234567"}'):
        wid, ts = str(uuid.uuid4()), str(int(time.time()))
        r = client.post(
            "/api/webhooks/whatnot",
            data=raw,
            headers={
                "webhook-id": wid,
                "webhook-timestamp": ts,
                "webhook-signature": "v1," + webhooks.sign(SECRET, wid, ts, raw),
            },
        )
        assert r.status_code == 400
    assert events(app) == []


def test_a_body_too_large_is_refused_before_it_is_read(app, client, connected, monkeypatch):
    monkeypatch.setattr(webhooks, "MAX_BYTES", 64)
    assert deliver(client).status_code == 413


@pytest.mark.parametrize(
    ("topic", "data", "ref"),
    [
        (
            "orders.updated",
            {"id": "12", "show_id": None, "buyer": {"username": "x"}},
            {"order_id": "12"},
        ),
        ("shows.created", {"id": "9b2f-1", "title": "<b>x</b>"}, {"show_id": "9b2f-1"}),
        ("shows.ended", {"show_id": "9b2f-1"}, {"show_id": "9b2f-1"}),
        (
            "shipments.created",
            {"id": "77", "documents": {"label_url": "https://x"}},
            {"shipment_id": "77"},
        ),
        (
            "shipments.label_generated",
            {"shipment_id": "77", "documents": {"label_url": "https://x"}},
            {"shipment_id": "77"},
        ),
        (
            "shipments.items.added",
            {"shipment_id": "77", "shipment_item_ids": ["1", "2"]},
            {"shipment_id": "77"},
        ),
        (
            "shipments.replaced",
            {"previous_shipment": {"id": "77"}, "replacement_shipment": {"id": "78"}},
            {"shipment_id": "77", "replaced_by": "78"},
        ),
        (
            "merchandising.products.added_to_show",
            {"product_id": "5", "show_id": "s-1"},
            {"product_id": "5", "show_id": "s-1"},
        ),
        ("products.deleted", {"id": "5"}, {"product_id": "5"}),
        ("orders.created", {"id": "1; DROP TABLE", "show_id": {"x": 1}}, {}),
        ("something.new", {"id": "1"}, {}),
    ],
)
def test_only_the_ids_an_event_names_are_kept(
    app, client, connected, monkeypatch, topic, data, ref
):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    assert deliver(client, topic, data).status_code == 204
    assert events(app) == [(topic, ref, "queued")]
    with app.app_context():
        stored = json.dumps([e.ref for e in db.session.scalars(select(WhatnotEvent))])
        assert "label_url" not in stored and "username" not in stored


def test_the_job_hands_an_event_to_its_topic_and_ignores_topics_nobody_handles(
    app, client, connected, monkeypatch
):
    seen = []
    monkeypatch.setitem(webhooks.HANDLERS, "orders.created", lambda e: seen.append(e.ref))
    deliver(client)  # JOBS_INLINE=1 in tests: handled at once
    deliver(client, "something.new", {})
    assert seen == [{"order_id": "1462657228", "show_id": "abc-1"}]
    assert [s for _, _, s in events(app)] == ["done", "ignored"]


def test_a_handler_that_fails_is_retried_by_the_queue_and_then_done(
    app, client, connected, monkeypatch
):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    calls = []

    def flaky(event):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Whatnot hiccup")

    monkeypatch.setitem(webhooks.HANDLERS, "orders.created", flaky)
    deliver(client)
    with app.app_context():
        job = jobs.run_once("t")
        assert job.status.value == "queued" and "hiccup" in job.error  # backs off, tries again
        assert events(app)[0][2] == "queued"
        job.run_at = db.func.now()
        db.session.commit()
        assert jobs.run_once("t").status.value == "done"
    assert events(app)[0][2] == "done" and len(calls) == 2


def test_a_rejected_token_settles_the_event_and_flags_the_connection(
    app, client, connected, monkeypatch
):
    from app.services.whatnot import Unauthorized

    def rejected(event):
        raise Unauthorized()

    monkeypatch.setitem(webhooks.HANDLERS, "orders.created", rejected)
    deliver(client)
    with app.app_context():
        e = db.session.scalar(select(WhatnotEvent))
        assert (e.status, e.error) == ("failed", "Unauthorized")
        assert db.session.scalar(select(WhatnotConnection)).problem == "token_rejected"
        assert db.session.scalar(select(Job)).status.value == "done"  # not retried for nothing


def test_old_deliveries_are_pruned_and_one_nobody_handled_is_settled(
    app, client, connected, monkeypatch
):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    for _ in range(3):
        deliver(client)
    with app.app_context():
        done, stuck, waiting = db.session.scalars(
            select(WhatnotEvent).order_by(WhatnotEvent.id)
        ).all()
        done.status = "done"
        done.received_at = db.func.now() - db.text("interval '31 days'")
        stuck.received_at = db.func.now() - db.text("interval '4 days'")  # its job gave up
        db.session.commit()
        swept = retention.sweep()
        assert (swept["whatnot_events"], swept["whatnot_events_stuck"]) == (1, 1)
        left = [
            (e.status, e.error)
            for e in db.session.scalars(select(WhatnotEvent).order_by(WhatnotEvent.id))
        ]
        assert left == [("failed", "never handled"), ("queued", None)]

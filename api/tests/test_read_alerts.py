"""Nobody should have to open Settings to learn that Whatnot reads went quiet.

A rejected token or a missing permission is cleared by the next read of the same kind that
works (it used to stay until a manager pressed "Check now"). Reads that keep failing, orders
that fall behind a live or just-ended show, and a live show nobody records are said in words
on every page, to every role. Everything here runs against the in-memory fake or plain rows.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db import db
from app.models import (
    ChatMessage,
    Show,
    WhatnotConnection,
    WhatnotEvent,
    WhatnotShow,
)
from app.services.whatnot import connection, sync, watch, webhooks
from app.services.whatnot.client import Forbidden, Unauthorized, Unavailable, client_for


def ago(**kw):
    return datetime.now(UTC) - timedelta(**kw)


@pytest.fixture
def shop(app, world, fake_whatnot):
    with app.app_context():
        db.session.add(
            WhatnotConnection(
                organization_id=world["org"],
                seller_id="1234567",
                username="host_shop",
                connected_at=ago(days=2),
                last_ok_at=ago(minutes=1),
            )
        )
        db.session.commit()
    return {**world, "fake": fake_whatnot}


def add_show(org, *, status="live", started=None, ended=None, synced=None, title="Friday night"):
    ws = WhatnotShow(
        organization_id=org,
        whatnot_id=str(uuid.uuid4()),
        title=title,
        status=status,
        started_at=started,
        ended_at=ended,
        orders_synced_at=synced,
    )
    db.session.add(ws)
    db.session.flush()
    return ws


def state(org, resource, *, ok=None, error=None, started="recent"):
    """A reading's bookkeeping row. A failing one was tried a couple of minutes ago unless
    `started` says otherwise (None: never tried by the timer)."""
    row = connection.sync_state(org, resource)
    row.last_ok_at, row.last_error = ok, error
    row.last_started_at = ago(minutes=2) if started == "recent" else started
    return row


def conn_of(org):
    return db.session.scalar(
        select(WhatnotConnection).where(WhatnotConnection.organization_id == org)
    )


def reads(org):
    """A read of Whatnot that works: it asks the fake API who the token belongs to."""

    def read():
        client_for(org).me()
        return {"seen": 0}

    return read


def failing(exc):
    def read():
        raise exc

    return read


# ---------- A24: a problem clears when the same kind of read works again ----------


def test_a_rejected_token_clears_when_a_read_works_again(app, shop):
    org = shop["org"]
    with app.app_context():
        with pytest.raises(Unauthorized):
            sync._run(org, "shows", failing(Unauthorized()))
        assert conn_of(org).problem == "token_rejected"
        sync._run(org, "shows", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None and conn_of(org).problem_at is None


def test_a_missing_permission_clears_only_when_that_same_read_works(app, shop):
    org = shop["org"]
    with app.app_context():
        with pytest.raises(Forbidden):
            sync._run(org, "shipments", failing(Forbidden()))
        assert conn_of(org).problem == "no_permission"
        sync._run(org, "orders", reads(org))  # another kind of read: says nothing
        db.session.commit()
        assert conn_of(org).problem == "no_permission"
        sync._run(org, "shipments", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None


def test_any_read_that_works_proves_the_token_and_ends_a_rejection(app, shop):
    """A rejected token is the whole token's: one read that works shows it is not rejected,
    even while a rarely run read (here the daily ledger) still holds its old failure."""
    org = shop["org"]
    with app.app_context():
        for resource in ("shows", "orders"):
            with pytest.raises(Unauthorized):
                sync._run(org, resource, failing(Unauthorized()))
        sync._run(org, "shows", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None
        assert connection.sync_state(org, "orders").last_error is None  # no stale failure left


def test_a_missing_permission_stays_while_another_flagging_read_still_fails(app, shop):
    org = shop["org"]
    with app.app_context():
        for resource in ("shows", "orders"):
            with pytest.raises(Forbidden):
                sync._run(org, resource, failing(Forbidden()))
        sync._run(org, "shows", reads(org))
        db.session.commit()
        assert conn_of(org).problem == "no_permission"  # orders still fails
        sync._run(org, "orders", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None


def test_a_busy_or_down_whatnot_is_not_a_problem_to_clear_or_to_flag(app, shop):
    org = shop["org"]
    with app.app_context():
        with pytest.raises(Unavailable):
            sync._run(org, "orders", failing(Unavailable("503")))
        assert conn_of(org).problem is None
        sync._run(org, "orders", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None


def test_a_token_that_became_another_account_is_not_cleared_by_reads(app, shop):
    """The check that found the token belongs to someone else: reads of that other account
    succeed, and must not make the warning go away."""
    org = shop["org"]
    with app.app_context():
        shop["fake"].seller["id"] = "9999999"
        connection.check(org)
        db.session.commit()
        assert conn_of(org).problem == "wrong_account"
        sync._run(org, "shows", reads(org))
        db.session.commit()
        assert conn_of(org).problem == "wrong_account"


def test_a_wrong_account_stays_even_after_old_rejections(app, shop):
    """The token was revoked (reads failed), then replaced by another seller's, and a manager
    pressed Check now. The reads that work next belong to the wrong account."""
    org = shop["org"]
    with app.app_context():
        for resource in ("shows", "orders"):
            with pytest.raises(Unauthorized):
                sync._run(org, resource, failing(Unauthorized()))
        shop["fake"].seller["id"] = "9999999"
        connection.check(org)
        db.session.commit()
        assert conn_of(org).problem == "wrong_account"
        sync._run(org, "shows", reads(org))
        db.session.commit()
        sync._run(org, "orders", reads(org))
        db.session.commit()
        assert conn_of(org).problem == "wrong_account"


def test_a_wrong_account_is_not_turned_into_a_plain_rejection(app, shop):
    """A later failed read must not swap the warning for one that reads can clear."""
    org = shop["org"]
    with app.app_context():
        shop["fake"].seller["id"] = "9999999"
        connection.check(org)
        db.session.commit()
        with pytest.raises(Unauthorized):
            sync._run(org, "orders", failing(Unauthorized()))
        connection.check(org)
        assert conn_of(org).problem == "wrong_account"
        sync._run(org, "orders", reads(org))
        db.session.commit()
        assert conn_of(org).problem == "wrong_account"


def test_the_right_account_back_ends_a_wrong_account(app, shop):
    org = shop["org"]
    with app.app_context():
        shop["fake"].seller["id"] = "9999999"
        connection.check(org)
        db.session.commit()
        shop["fake"].seller["id"] = "1234567"
        connection.check(org)
        db.session.commit()
        assert conn_of(org).problem is None


def test_a_wrong_account_blinds_orders_and_is_the_banner(app, client, shop, sign_in):
    from app.services.whatnot import orders as whatnot_orders

    assert "wrong_account" in whatnot_orders.BLIND
    org = shop["org"]
    with app.app_context():
        shop["fake"].seller["id"] = "9999999"
        connection.check(org)
        db.session.commit()
    sign_in(org, role="fulfillment")
    assert client.get("/api/dashboard/counts").get_json()["whatnotProblem"] is True


def test_a_401_blip_on_the_daily_ledger_does_not_keep_the_problem_all_night(app, shop):
    org = shop["org"]
    with app.app_context():
        with pytest.raises(Unauthorized):
            sync._run(org, "ledger", failing(Unauthorized()), flag=False)
        with pytest.raises(Unauthorized):
            sync._run(org, "orders", failing(Unauthorized()))
        assert conn_of(org).problem == "token_rejected"
        sync._run(org, "orders", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None
        assert connection.sync_state(org, "ledger").last_error is None


def test_a_read_that_works_ends_an_unreachable_note(app, shop):
    org = shop["org"]
    with app.app_context():
        connection.note_problem(conn_of(org), Unavailable("503"))
        assert conn_of(org).problem == "unreachable"
        sync._run(org, "shows", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None


def test_a_webhook_that_hits_a_rejected_token_is_cleared_by_the_next_good_read(app, shop):
    org = shop["org"]
    with app.app_context():
        connection.note_failure(org, Unauthorized(), resource="orders")
        assert conn_of(org).problem == "token_rejected"
        sync._run(org, "orders", reads(org))
        db.session.commit()
        assert conn_of(org).problem is None


def test_the_banner_goes_away_once_the_timer_reads_again(app, client, shop, sign_in, monkeypatch):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    from app.services import jobs

    sign_in(shop["org"], role="fulfillment")
    shop["fake"].token = "regenerated"
    with app.app_context():
        sync.schedule_all()
        jobs.run_once("t")
    assert client.get("/api/dashboard/counts").get_json()["whatnotProblem"] is True
    shop["fake"].token = "wn_pat_tk_test"
    with app.app_context():
        connection.sync_state(shop["org"], "shows").last_started_at = None  # due again
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
    assert client.get("/api/dashboard/counts").get_json()["whatnotProblem"] is False


# ---------- A22: reads that keep failing, orders that fall behind ----------


def test_a_quiet_healthy_shop_has_no_notice(app, shop):
    with app.app_context():
        state(shop["org"], "shows", ok=ago(minutes=3))
        add_show(shop["org"], started=ago(minutes=40), synced=ago(minutes=2))
        db.session.commit()
        assert watch.notice(shop["org"]) is None


def test_reads_that_keep_failing_are_said_once_they_have_for_a_while(app, shop):
    org = shop["org"]
    with app.app_context():
        state(org, "shows", ok=ago(minutes=20), error="Unavailable 503")  # one hiccup
        db.session.commit()
        assert watch.notice(org) is None
        state(org, "shows", ok=ago(minutes=50), error="Unavailable 503")
        db.session.commit()
        n = watch.notice(org)
        assert n["code"] == "reads_failing" and 49 <= n["minutes"] <= 51
        state(org, "shows", ok=ago(minutes=1))  # a read worked
        db.session.commit()
        assert watch.notice(org) is None


def test_a_failure_nobody_has_retried_lately_is_not_one_that_keeps_failing(app, shop):
    org = shop["org"]
    with app.app_context():
        state(org, "shows", ok=ago(hours=5), error="Unavailable 503", started=ago(hours=4))
        state(org, "orders", ok=ago(hours=5), error="Unavailable 503", started=None)
        db.session.commit()
        assert watch.notice(org) is None


def test_orders_that_never_worked_since_connecting_count_from_connecting(app, shop):
    org = shop["org"]
    with app.app_context():
        state(org, "orders", ok=None, error="Unavailable 503")
        conn_of(org).connected_at = ago(hours=3)
        db.session.commit()
        assert watch.notice(org)["code"] == "reads_failing"


def test_a_live_show_whose_orders_stopped_arriving_is_behind(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(hours=1), synced=ago(minutes=30))
        state(org, "orders", ok=ago(minutes=30))
        db.session.commit()
        n = watch.notice(org)
        assert n["code"] == "orders_behind" and 29 <= n["minutes"] <= 31
        ws.orders_synced_at = ago(minutes=2)
        db.session.commit()
        assert watch.notice(org) is None


def test_orders_arriving_by_webhook_are_not_behind(app, shop):
    org = shop["org"]
    with app.app_context():
        add_show(org, started=ago(hours=1), synced=ago(minutes=40))
        state(org, "orders", ok=ago(minutes=40))
        conn = conn_of(org)
        conn.last_event_at = ago(minutes=1)
        db.session.add(
            WhatnotEvent(
                organization_id=org,
                webhook_id="msg_a",
                topic="orders.created",
                ref={},
                status="done",
                received_at=ago(minutes=2),
                processed_at=ago(minutes=2),
            )
        )
        db.session.commit()
        assert watch.notice(org) is None


def test_events_that_arrive_but_are_never_handled_do_not_hide_a_stale_show(app, shop):
    org = shop["org"]
    with app.app_context():
        add_show(org, started=ago(hours=1), synced=ago(minutes=40))
        state(org, "orders", ok=ago(minutes=40))
        conn_of(org).last_event_at = ago(minutes=1)
        db.session.add(
            WhatnotEvent(
                organization_id=org,
                webhook_id="msg_b",
                topic="orders.created",
                ref={},
                status="queued",
                received_at=ago(minutes=2),
            )
        )
        db.session.commit()
        assert watch.notice(org)["code"] == "orders_behind"


def test_a_show_that_just_went_live_is_not_behind_yet(app, shop):
    with app.app_context():
        add_show(shop["org"], started=ago(minutes=8))
        db.session.commit()
        assert watch.notice(shop["org"]) is None


def test_a_show_that_ended_without_its_last_read_is_behind(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(
            org,
            status="ended",
            started=ago(hours=3),
            ended=ago(minutes=30),
            synced=ago(minutes=45),  # before it ended
        )
        db.session.commit()
        assert watch.notice(org)["code"] == "orders_behind"
        ws.orders_synced_at = ago(minutes=10)
        db.session.commit()
        assert watch.notice(org) is None


def test_an_old_show_left_unread_is_not_an_alert(app, shop):
    with app.app_context():
        add_show(shop["org"], status="ended", started=ago(days=3), ended=ago(days=2), synced=None)
        add_show(shop["org"], status="live", started=ago(days=2), synced=ago(days=2))  # forgotten
        db.session.commit()
        assert watch.notice(shop["org"]) is None


def test_a_rejected_token_is_the_banner_and_not_also_a_notice(app, shop):
    with app.app_context():
        connection.note_problem(conn_of(shop["org"]), Unauthorized())
        state(shop["org"], "shows", ok=ago(hours=2), error="Unauthorized")
        db.session.commit()
        assert watch.notice(shop["org"]) is None


def test_a_shop_not_connected_has_no_notice(app, world):
    with app.app_context():
        assert watch.notice(world["org"]) is None


def test_every_role_with_the_page_gets_the_notice_in_the_nav_counts(app, client, shop, sign_in):
    with app.app_context():
        add_show(shop["org"], started=ago(hours=1), synced=ago(minutes=30))
        state(shop["org"], "orders", ok=ago(minutes=30))
        db.session.commit()
    sign_in(shop["org"], role="fulfillment")
    body = client.get("/api/dashboard/counts").get_json()
    assert body["whatnotNotice"]["code"] == "orders_behind"
    assert body["whatnotProblem"] is False
    text = str(body)
    assert "Unavailable" not in text and "ADMIN" not in text


# ---------- A27: a live show nobody is recording ----------


def recording(org, ws, *, started, last_chat=None, link="auto", device=None):
    show = Show(
        organization_id=org,
        show_key=f"rec-{uuid.uuid4()}",
        started_at=started,
        show_date=started.date(),
        whatnot_show_id=ws.id,
        whatnot_link=link,
        created_at=started,  # the server first heard of it as it began, unless a test says
    )
    db.session.add(show)
    db.session.flush()
    if last_chat is not None:
        db.session.add(
            ChatMessage(
                show_id=show.id,
                username="viewer",
                body="hello",
                raw_text="viewer hello",
                captured_at=last_chat,
                received_at=last_chat,
                client_id=uuid.uuid4().hex[:16],
                seq=1,
            )
        )
    db.session.flush()
    return show


def codes(rows):
    return sorted(r["code"] for r in rows)


def test_a_live_show_with_no_laptop_recording_is_said_after_a_few_minutes(app, shop):
    org = shop["org"]
    with app.app_context():
        add_show(org, started=ago(minutes=2))
        db.session.commit()
        assert watch.live_gaps(org) == []
        db.session.rollback()
        db.session.execute(WhatnotShow.__table__.update().values(started_at=ago(minutes=6)))
        db.session.commit()
        rows = watch.live_gaps(org)
        assert codes(rows) == ["no_recording"] and rows[0]["showId"] is None


def test_the_show_made_for_orders_is_not_a_laptop(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(minutes=20))
        made = recording(org, ws, started=ago(minutes=20), link="made")
        db.session.commit()
        rows = watch.live_gaps(org)
        assert codes(rows) == ["no_recording"] and rows[0]["showId"] == made.id


def test_a_laptop_that_started_late_is_said_with_how_late(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(minutes=40))
        recording(org, ws, started=ago(minutes=28), last_chat=ago(seconds=20))
        db.session.commit()
        rows = watch.live_gaps(org)
        assert codes(rows) == ["late_recording"]
        assert 11 <= rows[0]["minutes"] <= 13


def test_a_laptop_on_time_with_fresh_chat_is_quiet(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(minutes=40))
        recording(org, ws, started=ago(minutes=39), last_chat=ago(seconds=30))
        db.session.commit()
        assert watch.live_gaps(org) == []


def test_a_laptop_whose_chat_stopped_is_said(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(minutes=60))
        recording(org, ws, started=ago(minutes=59), last_chat=ago(minutes=9))
        db.session.commit()
        rows = watch.live_gaps(org)
        assert codes(rows) == ["no_chat"] and 8 <= rows[0]["minutes"] <= 10


def test_a_paused_or_ended_show_needs_no_laptop(app, shop):
    org = shop["org"]
    with app.app_context():
        add_show(org, status="stopped", started=ago(hours=1))
        add_show(org, status="ended", started=ago(hours=3), ended=ago(hours=1))
        db.session.commit()
        assert watch.live_gaps(org) == []


def test_the_dashboard_and_the_show_page_carry_the_gap(app, client, shop, sign_in):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(minutes=30), title="Friday night")
        made = recording(org, ws, started=ago(minutes=30), link="made")
        db.session.commit()
        made_id = made.id
    sign_in(org, role="fulfillment")
    live = client.get("/api/dashboard").get_json()["attention"]["liveGaps"]
    assert [(g["code"], g["title"], g["showId"]) for g in live] == [
        ("no_recording", "Friday night", made_id)
    ]
    shown = client.get(f"/api/shows/{made_id}").get_json()["liveGaps"]
    assert [g["code"] for g in shown] == ["no_recording"]
    other = client.get(f"/api/shows/{shop['show']}").get_json()["liveGaps"]
    assert other == []


def test_a_shop_that_does_not_need_a_laptop_is_not_nagged(app, shop):
    """Listing mode with orders applied as they arrive: the winner, item and payment come
    from Whatnot, so a missing laptop loses nothing the sale depends on."""
    org = shop["org"]
    from app.models import Organization

    with app.app_context():
        add_show(org, started=ago(minutes=30))
        conn_of(org).orders_mode = "auto"
        db.session.get(Organization, org).sale_mode = "listing"
        db.session.commit()
        assert watch.live_gaps(org) == []


def test_a_laptop_that_only_pinged_is_not_a_recording(app, shop):
    org = shop["org"]
    from app.models import Organization
    from app.services.devices import create_device

    with app.app_context():
        device, _ = create_device(db.session.get(Organization, org), "laptop")
        device.last_seen_at = ago(seconds=10)
        add_show(org, started=ago(minutes=30))
        db.session.commit()
        assert codes(watch.live_gaps(org)) == ["no_recording"]


@pytest.mark.usefixtures("inline_jobs")
def test_a_delivery_clears_the_banner_only_when_it_really_read_from_whatnot(
    app, client, shop, monkeypatch
):
    """A delivery failing with a rejected token; one that does nothing leaves it; one that reads
    from Whatnot fine ends it."""
    import json
    import time

    from app.services.whatnot import webhooks
    from app.services.whatnot.fake import EXAMPLE_WEBHOOK_SECRET

    app.config["WHATNOT_WEBHOOK_SECRET"] = EXAMPLE_WEBHOOK_SECRET

    def post(topic):
        raw = json.dumps(
            {
                "type": topic,
                "timestamp": "2026-11-08T03:00:00.000Z",
                "data": {"id": "1462657228", "show_id": "abc-1"},
                "version": "v2",
                "seller_id": "1234567",
            }
        ).encode()
        wid, ts = str(uuid.uuid4()), str(int(time.time()))
        sig = "v1," + webhooks.sign(EXAMPLE_WEBHOOK_SECRET, wid, ts, raw)
        headers = {"webhook-id": wid, "webhook-timestamp": ts, "webhook-signature": sig}
        return client.post("/api/webhooks/whatnot", data=raw, headers=headers)

    def rejected(event):
        raise Unauthorized()

    monkeypatch.setitem(webhooks.HANDLERS, "orders.created", rejected)
    post("orders.created")
    with app.app_context():
        assert conn_of(shop["org"]).problem == "token_rejected"
    # A delivery whose handler makes no call to Whatnot proves nothing about the token.
    monkeypatch.setitem(webhooks.HANDLERS, "orders.created", lambda event: None)
    post("orders.created")
    with app.app_context():
        assert conn_of(shop["org"]).problem == "token_rejected"
        assert connection.sync_state(shop["org"], "orders").last_error.startswith("Unauthorized")

    # A handler that reads from Whatnot successfully (through the reading bookkeeping) does.
    def reads_whatnot(event):
        sync._run(shop["org"], "orders", reads(shop["org"]))

    monkeypatch.setitem(webhooks.HANDLERS, "orders.created", reads_whatnot)
    post("orders.created")
    with app.app_context():
        assert conn_of(shop["org"]).problem is None


# ---------- A55: the packing role can see why a list may be incomplete ----------


def test_the_slips_say_when_whatnots_orders_were_read_to_every_role(app, client, shop, sign_in):
    org = shop["org"]
    with app.app_context():
        ws = add_show(
            org, status="ended", started=ago(hours=3), ended=ago(hours=1), synced=ago(minutes=90)
        )
        db.session.get(Show, shop["show"]).whatnot_show_id = ws.id
        db.session.commit()
        ws_id = ws.id
    sign_in(org, role="fulfillment")
    url = f"/api/orders/shows/{shop['show']}/order-slips"
    read = client.get(url).get_json()["ordersRead"]
    assert read["final"] is False and read["at"] and read["live"] is False
    with app.app_context():
        db.session.get(WhatnotShow, ws_id).orders_synced_at = ago(minutes=5)
        db.session.commit()
    assert client.get(url).get_json()["ordersRead"]["final"] is True


def test_slips_of_a_show_whatnot_does_not_know_have_no_read_to_report(app, client, shop, sign_in):
    sign_in(shop["org"], role="fulfillment")
    body = client.get(f"/api/orders/shows/{shop['show']}/order-slips").get_json()
    assert body["ordersRead"] is None


def test_a_check_that_works_ends_what_a_rejected_token_failed(app, client, shop):
    """A webhook met a rejected token; the owner fixed it and pressed Check now. No page may
    keep saying "cannot read Whatnot" until orders happen to be read again."""
    org = shop["org"]
    with app.app_context():
        add_show(org, status="ended", started=ago(hours=5), ended=ago(hours=4), synced=ago(hours=4))
        connection.note_failure(org, Unauthorized(), resource="orders")
        state(org, "orders", ok=ago(hours=3), error="Unauthorized", started=ago(minutes=2))
        db.session.commit()
        connection.check(org)
        db.session.commit()
        assert conn_of(org).problem is None
        assert connection.sync_state(org, "orders").last_error is None
        assert watch.notice(org) is None


@pytest.mark.usefixtures("inline_jobs")
def test_a_delivery_of_another_kind_does_not_end_a_missing_permission(app, client, shop):
    """A delivery handled fine (here a product one) says nothing about a missing permission."""
    import json
    import time

    from app.services.whatnot.fake import EXAMPLE_WEBHOOK_SECRET

    app.config["WHATNOT_WEBHOOK_SECRET"] = EXAMPLE_WEBHOOK_SECRET
    org = shop["org"]
    with app.app_context():
        with pytest.raises(Forbidden):
            sync._run(org, "shows", failing(Forbidden()))
        db.session.commit()
    for topic in ("orders.created", "products.updated", "shows.ended"):
        raw = json.dumps(
            {
                "type": topic,
                "timestamp": "2026-11-08T03:00:00.000Z",
                "data": {"id": "1462657228", "show_id": "abc-1"},
                "version": "v2",
                "seller_id": "1234567",
            }
        ).encode()
        wid, ts = str(uuid.uuid4()), str(int(time.time()))
        sig = "v1," + webhooks.sign(EXAMPLE_WEBHOOK_SECRET, wid, ts, raw)
        headers = {"webhook-id": wid, "webhook-timestamp": ts, "webhook-signature": sig}
        client.post("/api/webhooks/whatnot", data=raw, headers=headers)
    with app.app_context():
        done = db.session.scalar(
            select(db.func.count())
            .select_from(WhatnotEvent)
            .where(WhatnotEvent.organization_id == org, WhatnotEvent.status == "done")
        )
        assert done >= 1  # at least one delivery really was handled
        assert conn_of(org).problem == "no_permission"


def test_stream_declines_close_sales_again_once_the_problem_is_gone(app, shop):
    """The money consequence of A24: while the connection is flagged, `decides()` is False
    and a decline read from the stream closes the sale itself. It must come back once a
    read works again, not stay off for the rest of the night."""
    from app.services.whatnot import orders as whatnot_orders

    org = shop["org"]
    with app.app_context():
        conn = conn_of(org)
        conn.orders_mode = "auto"
        conn.auto_since = ago(days=1)
        ws = add_show(org, started=ago(minutes=30), synced=ago(minutes=1))
        rec = recording(org, ws, started=ago(minutes=30), last_chat=ago(seconds=10))
        db.session.commit()
        assert whatnot_orders.decides(rec) is True
        with pytest.raises(Unauthorized):
            sync._run(org, "orders", failing(Unauthorized()))
        assert whatnot_orders.decides(rec) is False  # flagged: the stream decides again
        sync._run(org, "orders", reads(org))
        db.session.commit()
        assert whatnot_orders.decides(rec) is True


# ---------- review round: the notice must not be hidden by what is not orders ----------


def test_webhooks_of_other_kinds_do_not_hide_orders_that_stopped_arriving(app, shop):
    """Product or show deliveries keep the timer from polling a live show (the shop looks
    "quiet-free"), so they must not count as orders reaching the app."""
    org = shop["org"]
    with app.app_context():
        add_show(org, started=ago(hours=1), synced=ago(minutes=40))
        state(org, "orders", ok=ago(minutes=40))
        conn_of(org).last_event_at = ago(minutes=1)
        for i, topic in enumerate(("products.updated", "shows.updated")):
            db.session.add(
                WhatnotEvent(
                    organization_id=org,
                    webhook_id=f"msg_other_{i}",
                    topic=topic,
                    ref={},
                    status="done",
                    received_at=ago(minutes=2),
                    processed_at=ago(minutes=2),
                )
            )
        db.session.commit()
        n = watch.notice(org)
        assert n is not None and n["code"] == "orders_behind", n


def test_reading_another_show_does_not_hide_a_live_show_that_is_behind(app, shop):
    org = shop["org"]
    with app.app_context():
        add_show(org, started=ago(hours=1), synced=ago(minutes=40))  # live, stale
        add_show(
            org, status="ended", started=ago(hours=4), ended=ago(hours=2), synced=ago(minutes=1)
        )
        state(org, "orders", ok=ago(minutes=1))  # the shop-wide row, fresh from the ended show
        db.session.commit()
        n = watch.notice(org)
        assert n is not None and n["code"] == "orders_behind", n


def test_a_live_show_read_a_moment_ago_is_not_behind_whatever_the_shop_wide_row_says(app, shop):
    org = shop["org"]
    with app.app_context():
        add_show(org, started=ago(hours=1), synced=ago(minutes=2))
        state(org, "orders", ok=ago(hours=2))
        db.session.commit()
        assert watch.notice(org) is None


# ---------- review round: the laptop's clock is never the evidence ----------


def test_a_laptop_clock_ahead_neither_hides_silent_chat_nor_invents_a_late_start(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(minutes=20))
        show = recording(
            org, ws, started=ago(minutes=20) + timedelta(minutes=30), last_chat=ago(minutes=9)
        )
        show.created_at = ago(minutes=19)  # the server first heard from it a minute in
        db.session.commit()
        rows = watch.live_gaps(org)
        assert codes(rows) == ["no_chat"], rows


def test_a_laptop_clock_behind_does_not_hide_a_late_start(app, shop):
    org = shop["org"]
    with app.app_context():
        ws = add_show(org, started=ago(minutes=40))
        show = recording(
            org, ws, started=ago(minutes=40) - timedelta(minutes=30), last_chat=ago(seconds=20)
        )
        show.created_at = ago(minutes=28)  # the server first heard from it 12 minutes in
        db.session.commit()
        rows = watch.live_gaps(org)
        assert codes(rows) == ["late_recording"] and 11 <= rows[0]["minutes"] <= 13, rows


# ---------- review round: slips only warn about shows whose read can be judged ----------


def test_show_read_judges_only_live_and_ended_shows(app, shop):
    """A cancelled or scheduled show has no orders to be missing, and an ended one with no end
    time can never be shown "read since it ended" (the timer never picks it again): no strip,
    instead of a warning that never goes away."""
    org = shop["org"]
    with app.app_context():
        ended = add_show(org, status="ended", started=ago(hours=3), ended=ago(hours=1))
        live = add_show(org, started=ago(hours=1))
        assert watch.show_read(ended)["final"] is False
        assert watch.show_read(live)["live"] is True
        for ws in (
            add_show(org, status="cancelled"),
            add_show(org, status="scheduled"),
            add_show(org, status="ended", started=ago(hours=3), synced=ago(minutes=5)),
        ):
            assert watch.show_read(ws) is None


# ---------- review round 2: only a read that got an answer from Whatnot proves anything ----------


def test_a_read_that_made_no_call_does_not_end_a_rejection(app, shop):
    """Some reads ask nothing (no open orders to refresh): "worked" then says nothing about
    the token, and the stream's declines must not start deciding again because of it."""
    org = shop["org"]
    with app.app_context():
        with pytest.raises(Unauthorized):
            sync._run(org, "orders", failing(Unauthorized()))
        assert conn_of(org).problem == "token_rejected"
        sync._run(org, "open_orders", lambda: {"orders": 0})  # no call made
        db.session.commit()
        assert conn_of(org).problem == "token_rejected"
        sync._run(org, "open_orders", reads(org))  # now one that asked, and was answered
        db.session.commit()
        assert conn_of(org).problem is None


def test_the_timers_empty_open_orders_pass_does_not_clear_a_rejected_token(app, shop):
    org = shop["org"]
    with app.app_context():
        with pytest.raises(Unauthorized):
            sync._run(org, "orders", failing(Unauthorized()))
        state(org, "shows", ok=ago(minutes=1), started=ago(minutes=1))  # not due
        connection.sync_state(org, "open_orders").last_started_at = None  # due; none open
        db.session.commit()
        sync.sync_job({"org_id": org})
        db.session.commit()
        assert connection.sync_state(org, "open_orders").last_ok_at is not None
        assert conn_of(org).problem == "token_rejected"


def test_the_timers_own_read_stamps_a_live_show_so_it_is_not_behind(app, shop):
    """Nothing but the timer reads this live show (no deliveries): its own read time is the
    evidence the notice relies on, so a real pass must keep the notice away."""
    org = shop["org"]
    with app.app_context():
        add_show(org, started=ago(hours=1), synced=ago(minutes=40))
        state(org, "shows", ok=ago(minutes=1), started=ago(minutes=1))  # list not due
        db.session.commit()
        assert watch.notice(org)["code"] == "orders_behind"
        sync.sync_job({"org_id": org})
        db.session.commit()
        assert watch.notice(org) is None

import pytest
from sqlalchemy import select

from app.db import db
from app.models import (
    Job,
    JobStatus,
    Organization,
    ParsedEvent,
    ReviewItem,
    ReviewStatus,
    Sale,
    Sku,
)
from app.services import catalog, jobs, pipeline
from tests.conftest import SHOW  # noqa: F401 - shared show key


def run(app, show_id, **kw):
    with app.app_context():
        stats = pipeline.process_show(show_id, **kw)
        db.session.commit()
        return stats


def level(app, sku_id):
    with app.app_context():
        s = db.session.get(Sku, sku_id)
        return s.on_hand, s.reserved


def sales(app, show_id):
    with app.app_context():
        return [
            (s.buyer_username, s.status.value, s.sku.code if s.sku else None, s.quantity)
            for s in db.session.scalars(
                select(Sale).where(Sale.show_id == show_id).order_by(Sale.id)
            )
        ]


def open_reviews(app):
    with app.app_context():
        return [
            (r.reason, r.sale_id)
            for r in db.session.scalars(
                select(ReviewItem)
                .where(ReviewItem.status == ReviewStatus.open)
                .order_by(ReviewItem.id)
            )
        ]


def test_wins_are_paid_at_once_and_cancellations_restock(app, world):
    world["say"](
        app,
        [
            ("", "Auction #1: Red bundle"),
            ("lani808", "mine"),
            ("", "lani808 won the auction for $25"),
            ("", "pokemom_22 won Red bundle x2"),
            ("", "cancelled for pokemom_22"),
        ],
    )
    stats = run(app, world["show"])
    assert (stats.parsed, stats.sales) == (5, 2)
    # Whatnot charges every win: lani808's sale is paid (stock committed); pokemom_22's was
    # paid too, then cancelled before picking, so its 2 went back on the shelf.
    assert sales(app, world["show"]) == [
        ("lani808", "paid", "RED", 1),
        ("pokemom_22", "cancelled", "RED", 2),
    ]
    assert level(app, world["red"]) == (2, 0)
    with app.app_context():
        kinds = [
            e.kind.value for e in db.session.scalars(select(ParsedEvent).order_by(ParsedEvent.id))
        ]
        assert kinds == ["listing", "claim", "sale", "sale", "cancel"]
        first = db.session.scalar(select(Sale).order_by(Sale.id))
        assert (
            first.item_text == "Red bundle" and first.price_cents == 2500
        )  # item from the listing context
    # Running again changes nothing.
    stats = run(app, world["show"])
    assert stats.parsed == 0
    assert level(app, world["red"]) == (2, 0)


def test_unmatched_item_goes_to_review_and_assigning_a_sku_sells_it_and_remembers(app, world):
    world["say"](app, [("", "cardguy bought mystery box for $10")])
    run(app, world["show"])
    assert sales(app, world["show"]) == [("cardguy", "detected", None, 1)]
    assert [r for r, _ in open_reviews(app)] == ["unmatched_item"]
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        from app.models import User

        user = User(
            organization_id=world["org"],
            email="o@x.test",
            name="O",
            password_hash="x",
            role="owner",
            session_token="t",
        )
        db.session.add(user)
        db.session.flush()
        pipeline.resolve(item, "assign_sku", {"skuId": world["red"], "remember": True}, user=user)
        db.session.commit()
    # assigning the SKU reserves and then autopays (the win was charged)
    assert sales(app, world["show"]) == [("cardguy", "paid", "RED", 1)]
    assert level(app, world["red"]) == (2, 0)
    assert open_reviews(app) == []
    with app.app_context():
        assert catalog.resolve_alias(world["org"], "mystery box").code == "RED"
    # Next show: the alias is known, no review needed.
    world["say"](app, [("", "kat.c bought mystery box")])
    run(app, world["show"])
    assert sales(app, world["show"])[-1] == ("kat.c", "paid", "RED", 1)


def test_overselling_sells_without_a_review(app, world):
    world["say"](app, [("", "lani808 won Red bundle x5")])
    run(app, world["show"])
    assert sales(app, world["show"]) == [("lani808", "paid", "RED", 5)]
    assert open_reviews(app) == []
    assert level(app, world["red"]) == (-2, 0)  # 2 short: the shop buys more
    with app.app_context():
        assert db.session.get(Sku, world["red"]).short == 2


def test_ambiguous_cancellation_asks_which_sale(app, world):
    world["say"](
        app,
        [
            ("", "lani808 won Red bundle"),
            # Two separate wins: the identical line twice would be one line captured twice
            # (tests/test_duplicate_sales.py).
            ("", "lani808 won Red bundle for $25"),
            ("host_shop", "cancelled for lani808"),
        ],
    )
    run(app, world["show"])
    reviews = open_reviews(app)
    assert [r for r, _ in reviews] == ["ambiguous_sale"]
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert len(item.proposal["candidates"]) == 2
        pick = item.proposal["candidates"][0]["saleId"]
        from app.models import User

        user = User(
            organization_id=world["org"],
            email="o@x.test",
            name="O",
            password_hash="x",
            role="owner",
            session_token="t",
        )
        db.session.add(user)
        db.session.flush()
        pipeline.resolve(item, "pick_sale", {"saleId": pick}, user=user)
        db.session.commit()
    assert sorted(s[1] for s in sales(app, world["show"])) == ["cancelled", "paid"]
    assert level(app, world["red"]) == (2, 0)  # the cancelled one went back on the shelf


def test_cancel_with_no_open_sale_and_low_confidence_sale_go_to_review(app, world):
    world["say"](app, [("host_shop", "cancelled for nobody"), ("randomguy", "sold to randomguy")])
    run(app, world["show"])
    assert sorted(r for r, _ in open_reviews(app)) == ["low_confidence", "no_open_sale"]
    assert sales(app, world["show"]) == []


def test_reparse_only_revisits_dead_end_lines(app, world):
    world["say"](app, [("", "lani808 won Red bundle"), ("lani808", "thanks")])
    run(app, world["show"])
    with app.app_context():
        before = db.session.scalar(select(Sale)).id
        n_events = db.session.scalar(select(db.func.count()).select_from(ParsedEvent))
    stats = run(app, world["show"], reparse=True)
    assert stats.parsed == 1  # only the chatter line was re-evaluated
    with app.app_context():
        assert db.session.scalar(select(Sale)).id == before
        assert db.session.scalar(select(db.func.count()).select_from(ParsedEvent)) == n_events
    assert level(app, world["red"]) == (2, 0)


# ---------- jobs ----------


def test_jobs_dedupe_claim_run_and_retry(app, monkeypatch):
    monkeypatch.delenv("JOBS_INLINE", raising=False)
    calls = []

    @jobs.handler("test_ok")
    def _ok(payload):
        calls.append(payload)

    @jobs.handler("test_boom")
    def _boom(payload):
        raise RuntimeError("boom")

    with app.app_context():
        a = jobs.enqueue("test_ok", {"x": 1}, dedupe_key="k")
        b = jobs.enqueue("test_ok", {"x": 2}, dedupe_key="k")
        assert a.id == b.id  # deduped while queued
        boom = jobs.enqueue("test_boom", {})
        db.session.commit()
        assert jobs.run_once("w1").status is JobStatus.done
        assert calls == [{"x": 1}]
        j = jobs.run_once("w1")
        assert (
            j.id == boom.id
            and j.status is JobStatus.queued
            and "boom" in j.error
            and j.attempts == 1
        )
        assert jobs.run_once("w1") is None  # retry is in the future
        assert (
            jobs.enqueue("test_ok", {"x": 3}, dedupe_key="k").id != a.id
        )  # a is done, so a new one


def test_inline_jobs_run_at_enqueue(app, monkeypatch):
    monkeypatch.setenv("JOBS_INLINE", "1")
    seen = []

    @jobs.handler("test_inline")
    def _h(payload):
        seen.append(payload)

    with app.app_context():
        j = jobs.enqueue("test_inline", {"a": 1})
        assert seen == [{"a": 1}]
        assert db.session.get(Job, j.id).status is JobStatus.done


def test_ingest_schedules_a_parse_and_inline_mode_parses_immediately(
    client, app, monkeypatch, world
):
    monkeypatch.setenv("JOBS_INLINE", "1")
    with app.app_context():
        from app.services.devices import create_device

        _, key = create_device(db.session.get(Organization, world["org"]), "L")
        db.session.commit()
    rows = [
        {
            "id": f"{i:016x}",
            "showId": SHOW,
            "username": "",
            "body": b,
            "rawText": b,
            "capturedAt": SHOW,
            "seq": i,
        }
        for i, b in enumerate(["lani808 won Red bundle", "kat.c won Red bundle"], start=1)
    ]
    r = client.post(
        "/api/ingest",
        json={"version": 1, "show": {"id": SHOW, "startedAt": SHOW}, "rows": rows},
        headers={"Authorization": "Bearer " + key},
    )
    assert r.status_code == 200
    assert sales(app, world["show"]) == [
        ("lani808", "paid", "RED", 1),
        ("kat.c", "paid", "RED", 1),
    ]
    with app.app_context():
        assert db.session.scalar(select(Job)).status is JobStatus.done


def test_review_and_show_routes(client, app, world):
    world["say"](app, [("", "cardguy bought mystery box for $10"), ("", "lani808 won Red bundle")])
    r = client.get("/api/auth/csrf")
    h = {"X-CSRF-Token": r.get_json()["token"]}
    client.post(
        "/api/auth/signup",
        json={
            "organization": "Other",
            "name": "O",
            "email": "o@x.test",
            "password": "correct-horse-battery",
        },
        headers=h,
    )
    assert client.get(f"/api/shows/{world['show']}").status_code == 404  # other org
    # become a user of the world org
    with app.app_context():
        from app.models import User

        u = db.session.scalar(select(User))
        u.organization_id = world["org"]
        db.session.commit()
    assert client.post(f"/api/shows/{world['show']}/parse", headers=h).get_json()["sales"] == {
        "detected": 1,
        "paid": 1,
    }
    ev = client.get(f"/api/shows/{world['show']}/events").get_json()
    assert [e["kind"] for e in ev["events"]] == ["sale", "sale"]
    s = client.get(f"/api/shows/{world['show']}/sales").get_json()["sales"]
    assert s[0]["buyer"] == "cardguy" and s[0]["sku"] is None and s[1]["sku"]["code"] == "RED"
    items = client.get("/api/review").get_json()["items"]
    assert (
        len(items) == 1 and items[0]["reason"] == "unmatched_item" and len(items[0]["context"]) == 2
    )
    r = client.post(
        f"/api/review/{items[0]['id']}/resolve",
        json={"action": "assign_sku", "skuId": world["red"], "remember": False},
        headers=h,
    )
    assert r.status_code == 200, r.get_json()
    assert client.get("/api/review").get_json()["items"] == []
    assert (
        client.post(
            f"/api/review/{items[0]['id']}/resolve", json={"action": "dismiss"}, headers=h
        ).status_code
        == 409
    )
    stats = client.get(f"/api/shows/{world['show']}").get_json()["stats"]
    assert stats["sales"] == {"paid": 2} and stats["openReviews"] == 0
    actions = [e["action"] for e in client.get("/api/audit").get_json()["entries"]]
    assert "review.resolved" in actions and "show.parsed" in actions


def test_split_winner_line_waits_in_review_instead_of_becoming_a_sale(app, world):
    world["say"](app, [("", "Auction #1: Red bundle"), ("lani808", "won the auction!")])
    run(app, world["show"])
    assert sales(app, world["show"]) == []
    assert level(app, world["red"]) == (3, 0)
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        assert item.reason == "low_confidence"
        assert "winner line for lani808" in item.summary and "sender" in item.summary


def _owner(org_id):
    from app.models import User

    existing = db.session.scalar(select(User).where(User.email == "o@x.test"))
    if existing is not None:
        return existing
    user = User(
        organization_id=org_id,
        email="o@x.test",
        name="O",
        password_hash="x",
        role="owner",
        session_token="t",
    )
    db.session.add(user)
    db.session.flush()
    return user


def test_a_person_can_record_a_low_confidence_line_as_a_sale(app, world):
    """The human half of the split-winner-line fix: Review's "Record as a sale" goes through
    create_sale (SKU match from the listing, reserve + autopay, order), once, and never twice."""
    world["say"](
        app,
        [
            ("", "Auction #1: Red bundle"),
            ("lani808", "won the auction!"),  # platform line, winner arrived as the sender
            ("randomguy", "sold to randomguy"),  # a viewer saying it: still only a person decides
            ("host_shop", "cancelled for nobody"),  # not a sale line at all
        ],
    )
    run(app, world["show"])
    assert sales(app, world["show"]) == []
    with app.app_context():
        user = _owner(world["org"])
        items = {
            r.summary: r
            for r in db.session.scalars(
                select(ReviewItem).where(ReviewItem.status == ReviewStatus.open)
            )
        }
        split = next(r for r in items.values() if "lani808" in r.summary)
        viewer = next(r for r in items.values() if "randomguy" in r.summary)
        no_sale = next(r for r in items.values() if r.reason == "no_open_sale")

        pipeline.resolve(split, "confirm_sale", {}, user=user)
        assert split.status is ReviewStatus.resolved
        assert split.resolution["action"] == "confirm_sale" and split.resolution["reserved"]
        assert split.resolved_by_id == user.id

        # A line that is not a sale cannot be recorded as one.
        with pytest.raises(pipeline.ReviewError) as e:
            pipeline.resolve(no_sale, "confirm_sale", {}, user=user)
        assert "not a sale line" in str(e.value)

        # The viewer-shaped line: recorded with the item from the listing, no SKU price in chat.
        pipeline.resolve(viewer, "confirm_sale", {}, user=user)
        db.session.commit()

    # Both wins are real sales now: item from the listing context, paid at the win (stock
    # committed), one order each.
    assert sales(app, world["show"]) == [
        ("lani808", "paid", "RED", 1),
        ("randomguy", "paid", "RED", 1),
    ]
    assert level(app, world["red"]) == (1, 0)
    assert [r for r, _ in open_reviews(app)] == ["no_open_sale"]  # the cancel, untouched
    with app.app_context():
        from app.models import Order

        assert sorted(
            o.buyer_username
            for o in db.session.scalars(select(Order).where(Order.show_id == world["show"]))
        ) == ["lani808", "randomguy"]
        # Resolving again is refused (the item is closed), and so is recording the same
        # line through a second item: one sale per chat line, ever.
        split = db.session.scalar(select(ReviewItem).where(ReviewItem.summary.contains("lani808")))
        with pytest.raises(pipeline.ReviewError):
            pipeline.resolve(split, "confirm_sale", {}, user=_owner(world["org"]))
        again = pipeline.open_review(
            world["org"],
            "low_confidence",
            "same line again",
            show_id=world["show"],
            message_id=split.message_id,
            event_id=split.event_id,
            dedupe=False,
        )
        with pytest.raises(pipeline.ReviewError) as e:
            pipeline.resolve(again, "confirm_sale", {}, user=_owner(world["org"]))
        assert "already sale" in str(e.value)
        db.session.rollback()
    # Re-parsing never touches the confirmed sales.
    run(app, world["show"], reparse=True)
    assert len(sales(app, world["show"])) == 2


def test_recording_a_sale_with_an_unknown_item_opens_the_sku_question(app, world):
    world["say"](app, [("", "Auction #2: Mystery box"), ("kat.c", "won the auction!")])
    run(app, world["show"])
    with app.app_context():
        item = db.session.scalar(select(ReviewItem))
        pipeline.resolve(item, "confirm_sale", {}, user=_owner(world["org"]))
        db.session.commit()
    assert sales(app, world["show"]) == [("kat.c", "detected", None, 1)]
    assert [r for r, _ in open_reviews(app)] == ["unmatched_item"]
    assert level(app, world["red"]) == (3, 0)

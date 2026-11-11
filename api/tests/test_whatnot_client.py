"""The Whatnot client against the in-memory fake: pacing, paging, retries, errors, money.

The fake follows Whatnot's published OpenAPI description. Nothing here has run against the
live Seller API (there is no sandbox), so these prove our side of the contract only.
"""

import pytest

from app.services.whatnot import (
    Busy,
    Conflict,
    Forbidden,
    Invalid,
    NotConfigured,
    NotFound,
    Unauthorized,
    Unavailable,
    WhatnotClient,
    cents,
    make_client,
    when,
)
from app.services.whatnot.client import Response
from app.services.whatnot.fake import TOKEN, FakeWhatnot


def client(fake, **kw):
    sleeps = kw.pop("sleeps", [])
    return WhatnotClient(TOKEN, transport=fake.transport, sleep=sleeps.append, **kw), sleeps


@pytest.mark.parametrize(
    ("money", "expected"),
    [
        ({"amount": "42.00", "currency": "USD"}, 4200),
        ({"amount": "0.07", "currency": "USD"}, 7),
        ({"amount": "1234.5", "currency": "USD"}, 123450),
        ({"amount": "-3.10", "currency": "USD"}, -310),
        ({"amount": "19.99", "currency": "USD"}, 1999),  # 19.99 * 100 is 1998.99… as a float
        ({"amount": "0.29", "currency": "USD"}, 29),
        ({"amount": 5, "currency": "USD"}, 500),
        ({"amount": "abc"}, None),
        ({"amount": "NaN"}, None),
        ({"amount": True}, None),
        ({}, None),
        (None, None),
        ("12.00", None),
    ],
)
def test_money_becomes_integer_cents_without_a_float(money, expected):
    assert cents(money) == expected


def test_timestamps_are_tz_aware():
    assert when("2026-10-20T16:00:00.123Z").isoformat() == "2026-10-20T16:00:00.123000+00:00"
    assert when("2026-10-20T16:00:00").tzinfo is not None
    assert when("nope") is None and when(None) is None and when(12) is None


def test_reads_page_through_every_item_newest_first():
    fake = FakeWhatnot()
    show = fake.add_show()
    ids = [fake.add_order(show_id=show["id"], buyer=f"b{i}", items=[{}])["id"] for i in range(7)]
    c, _ = client(fake)
    got = [o["id"] for o in c.orders(limit=3)]
    assert got == list(reversed(ids))  # newest first, across three pages
    assert [p for m, p in fake.calls if p == "/orders"] == ["/orders"] * 3
    assert [o["id"] for o in c.orders(limit=3, reverse=False)] == ids


def test_a_reader_can_stop_early_and_no_further_page_is_fetched():
    fake = FakeWhatnot()
    for i in range(9):
        fake.add_order(show_id=None, buyer=f"b{i}", items=[{}])
    c, _ = client(fake)
    for n, _order in enumerate(c.orders(limit=2), start=1):
        if n == 3:
            break
    assert len(fake.calls) == 2


def test_a_429_waits_what_whatnot_says_then_succeeds():
    fake = FakeWhatnot()
    fake.fail_next(429, "rate-limited", retry_after="2")
    fake.fail_next(429, "rate-limited", retry_after="2")
    c, sleeps = client(fake)
    assert c.me()["username"] == "host_shop"
    assert len(sleeps) == 2 and 2 <= sleeps[0] < 2.3 and 4 <= sleeps[1] < 4.3  # doubles
    assert c.calls == 3


def test_a_429_that_never_clears_becomes_busy():
    fake = FakeWhatnot()
    for _ in range(4):
        fake.fail_next(429, "rate-limited", retry_after="1")
    c, sleeps = client(fake)
    with pytest.raises(Busy) as e:
        c.me()
    assert len(sleeps) == 3 and e.value.status == 429
    assert "slow down" in e.value.words


def test_server_errors_retry_reads_but_never_a_write_that_is_not_safe_to_repeat():
    fake = FakeWhatnot()
    fake.fail_next(503, "service-unavailable")
    c, sleeps = client(fake)
    assert c.me()["id"] == "1234567" and len(sleeps) == 1

    order = fake.add_order(show_id=None, buyer="b", items=[{}])
    fake.fail_next(500, "internal-error")
    with pytest.raises(Unavailable):
        c.request("POST", f"/orders/{order['id']}/cancel", json={"reason": "other"})
    assert fake.cancelled == []  # sent once, failed, not sent again
    assert [m for m, _ in fake.calls].count("POST") == 1

    fake.fail_next(500, "internal-error")
    c.request("POST", "/inventory/set", json={"items": []}, safe=True)  # carries its own keys
    assert [m for m, _ in fake.calls].count("POST") == 3


def test_a_dropped_connection_is_retried_for_reads():
    fake = FakeWhatnot()
    attempts = []

    def flaky(method, url, **kw):
        attempts.append(url)
        if len(attempts) < 3:
            raise Unavailable("ConnectionError calling Whatnot")
        return fake.transport(method, url, **kw)

    sleeps = []
    c = WhatnotClient(TOKEN, transport=flaky, sleep=sleeps.append)
    assert c.me()["username"] == "host_shop" and len(sleeps) == 2


@pytest.mark.parametrize(
    ("status", "problem", "error"),
    [
        (401, "unauthenticated", Unauthorized),
        (403, "missing-scope", Forbidden),
        (404, "order-not-found", NotFound),
        (409, "order-not-cancellable", Conflict),
        (422, "invalid-parameter", Invalid),
        (400, "malformed-request", Invalid),
    ],
)
def test_problem_types_become_typed_errors_with_plain_words(status, problem, error):
    fake = FakeWhatnot()
    fake.fail_next(status, problem, required_scopes=["read:orders"])
    c, sleeps = client(fake)
    with pytest.raises(error) as e:
        c.order("1")
    err = e.value
    assert err.status == status and err.problem == problem and err.request_id
    assert sleeps == []  # a 4xx is never retried
    # What a person reads never carries Whatnot's own text, a scope name or the token.
    for text in (err.words, str(err)):
        assert "from Whatnot" not in text and "read:orders" not in text and TOKEN not in text
    if error is Forbidden:
        assert err.required_scopes == ["read:orders"]


def test_a_422_validation_error_with_a_list_detail_is_handled():
    fake = FakeWhatnot()
    fake._failures.append(Response(422, {"detail": [{"loc": ["query", "limit"], "msg": "x"}]}))
    c, _ = client(fake)
    with pytest.raises(Invalid) as e:
        c.order("1")
    assert e.value.detail == "" and e.value.problem == ""


def test_a_wrong_token_is_unauthorized_and_the_token_never_shows():
    fake = FakeWhatnot()
    c = WhatnotClient("wn_pat_tk_wrong", transport=fake.transport)
    with pytest.raises(Unauthorized) as e:
        c.me()
    assert "wn_pat_tk" not in repr(c) + str(e.value) + e.value.words
    with pytest.raises(NotConfigured):
        WhatnotClient("")


def test_every_request_carries_the_token_and_a_request_id():
    seen = {}

    def spy(method, url, *, headers, params, json):
        seen.update(headers=headers, url=url, params=params)
        return Response(200, {"data": [], "next_cursor": None})

    c = WhatnotClient(TOKEN, transport=spy)
    list(c.shows("ended"))
    assert seen["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert len(seen["headers"]["X-Request-ID"]) == 32
    assert seen["url"] == "https://api.whatnot.com/seller-api/rest/v2/shows"
    assert seen["params"] == {"status": "ended", "limit": 50}  # no empty cursor, no None


def test_the_throttle_runs_before_every_attempt():
    fake = FakeWhatnot()
    fake.fail_next(503)
    ticks = []
    c = WhatnotClient(
        TOKEN, transport=fake.transport, throttle=lambda: ticks.append(1), sleep=lambda s: None
    )
    c.me()
    assert len(ticks) == 2


def test_a_test_cannot_reach_whatnot_without_a_fake(app):
    with app.app_context():
        with pytest.raises(RuntimeError, match="FakeWhatnot"):
            make_client()


def test_no_token_configured_is_said_in_words(app, fake_whatnot):
    app.config["WHATNOT_API_TOKEN"] = ""
    with app.app_context():
        with pytest.raises(NotConfigured) as e:
            make_client()
    assert "WHATNOT" not in e.value.words  # never the setting's name


def test_the_shared_budget_makes_the_ninth_request_in_a_second_wait(app, monkeypatch):
    from app.services.whatnot import throttle

    monkeypatch.setattr(throttle.time, "time", lambda: 1000.25)  # mid-second
    counts = iter([1, 8, 9, 10, 1])
    monkeypatch.setattr(throttle, "_count", lambda key: next(counts))
    sleeps = []
    with app.app_context():
        throttle.acquire(7, sleep=sleeps.append)  # 1st of the second: straight through
        throttle.acquire(7, sleep=sleeps.append)  # 8th: still fine
        throttle.acquire(7, sleep=sleeps.append)  # 9th and 10th: wait for the next second
    assert len(sleeps) == 2 and all(0.75 < s < 0.85 for s in sleeps)


def test_the_shared_budget_counts_in_postgres_and_gives_up_in_words(app):
    from app.services.whatnot import throttle

    with app.app_context():
        assert throttle._count("whatnot-out:org:99") >= 1
        waits = []
        with pytest.raises(Busy):
            throttle.acquire(99, per_second=0, sleep=waits.append, max_wait=0.5)
        assert waits  # it waited before giving up

"""`flask whatnot-probe` reads only and prints shapes, never the shop's data."""

import pytest

from app.services.whatnot import WhatnotClient, probe
from app.services.whatnot.fake import TOKEN, FakeWhatnot


def shop() -> FakeWhatnot:
    fake = FakeWhatnot(username="think_electronics")
    show = fake.add_show(title="SECRET SHOW TITLE")
    for i in range(4):
        order = fake.add_order(
            show_id=show["id"],
            buyer=f"buyer_name_{i}",
            items=[{"title": f"CHOOSE ANY PRODUCT YOU WANT #{70 + i}", "price_cents": 1700 + i}],
        )
        fake.add_shipment(
            recipient=f"buyer_name_{i}", order_ids=[order["id"]], status="label_generated"
        )
    fake.add_product(
        title="Hidden product", variants=[{"sku": "RED", "price_cents": 2500, "quantity": 3}]
    )
    return fake


def test_the_probe_reports_shapes_and_none_of_the_shops_data():
    fake = shop()
    seen: dict = {}
    client = WhatnotClient(
        TOKEN, transport=probe.read_only(fake.transport, sleep=lambda s: None, seen=seen)
    )
    report = "\n".join(probe.run(client, seen))
    assert "order id is decimal, 10 digits" in report and "show id is uuid" in report
    assert "'pending_fulfillment': 4" in report and "'auction': 4" in report
    assert 'ends in a lot number (#N), says "choose/pick"' in report
    assert "buyer filled: 4/4" in report and "label link filled: 4/4" in report
    assert "order_id filled: 3/3" in report
    assert "orders since the latest show's start: 4, about 1 page(s) of 50" in report
    # Nothing that identifies a buyer, a product, a parcel or the account.
    for secret in ("buyer_name", "think_electronics", "SECRET", "CHOOSE ANY", "Hidden", "17.0",
                   "labels.example", "http", TOKEN, "1234567"):  # fmt: skip
        assert secret not in report
    for order_id in fake.orders:
        assert order_id not in report
    assert {m for m, _ in fake.calls} == {"GET"} and len(fake.calls) <= probe.MAX_CALLS


def test_the_probe_cannot_write_and_stops_at_its_budget(monkeypatch):
    fake = shop()
    client = WhatnotClient(TOKEN, transport=probe.read_only(fake.transport, sleep=lambda s: None))
    with pytest.raises(RuntimeError, match="read-only"):
        client.request("POST", "/shipments/1/generate_label")
    assert fake.calls == [] and fake.labels_requested == []

    monkeypatch.setattr(probe, "MAX_CALLS", 3)
    seen: dict = {}
    client = WhatnotClient(
        TOKEN, transport=probe.read_only(fake.transport, sleep=lambda s: None, seen=seen)
    )
    report = "\n".join(probe.run(client, seen))
    assert len(fake.calls) == 3 and "call budget is used up" in report


def test_a_missing_permission_names_what_the_token_needs():
    fake = shop()
    fake.scopes = {"read:shows", "read:orders"}
    client = WhatnotClient(TOKEN, transport=probe.read_only(fake.transport, sleep=lambda s: None))
    report = "\n".join(probe.run(client))
    assert "buyer filled: 0/4" in report  # no read:customers: buyers come back empty
    assert "the token needs: read:shipments, write:shipments" in report


def test_the_command_refuses_without_a_token_and_runs_with_one(app, fake_whatnot, monkeypatch):
    monkeypatch.setattr(probe.time, "sleep", lambda s: None)
    runner = app.test_cli_runner()
    ok = runner.invoke(args=["whatnot-probe"])
    assert ok.exit_code == 0 and "## Account" in ok.output and "LIVE shop" in ok.output
    app.config["WHATNOT_API_TOKEN"] = ""
    refused = runner.invoke(args=["whatnot-probe"])
    assert refused.exit_code == 1 and "not set up" in refused.output

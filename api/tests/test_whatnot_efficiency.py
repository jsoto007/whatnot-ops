"""What the Whatnot work costs, against the in-memory fake (claude-reports/Efficiency-Review):
applying a show's orders again must not cost a statement per order, several shows are read
in one pass, a "choose any" lot's listing is not read during the show, and a labelled parcel's
items are not asked for again."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db import db
from app.models import Show, WhatnotConnection, WhatnotOrder, WhatnotShipment, WhatnotShow
from app.services.whatnot import catalog as listings
from app.services.whatnot import client_for, shipping, sync
from app.services.whatnot import orders as whatnot_orders
from tests import test_query_counts as qc
from tests import test_whatnot_orders as wo
from tests.test_whatnot_orders import order, read, snapshot

shop = wo.shop  # the connected shop, its show matched to an ended show on Whatnot
count_statements = qc.count_statements

SLACK = 2


def _orders(shop, start: int, stop: int) -> None:
    for i in range(start, stop):
        order(shop, f"buyer{i}", f"CHOOSE ANY PRODUCT YOU WANT #{i}", price=1000 + i)


def _apply_counted(app, shop, count_statements) -> int:
    with app.app_context():
        show = db.session.get(Show, shop["show"])
        with count_statements() as n:
            whatnot_orders.apply(show)
        db.session.commit()
        return n.count


def test_applying_again_costs_the_same_with_5_orders_or_40_and_changes_nothing(
    app, shop, count_statements
):
    _orders(shop, 0, 5)
    read(app, shop)
    _apply_counted(app, shop, count_statements)  # the first apply records the sales
    few = _apply_counted(app, shop, count_statements)
    _orders(shop, 5, 40)
    read(app, shop)
    _apply_counted(app, shop, count_statements)
    before = snapshot(app)
    many = _apply_counted(app, shop, count_statements)
    assert snapshot(app) == before  # nothing to do, nothing done
    assert many <= few + SLACK, (few, many)


def test_several_shows_are_read_in_one_pass_and_each_is_marked_read(app, shop):
    fake = shop["fake"]
    now = shop["now"]
    older = fake.add_show(
        title="Thursday night",
        start=now - timedelta(days=1, hours=3),
        ended=now - timedelta(days=1),
    )
    for i in range(60):
        order(shop, f"buyer{i}", f"Lot #{i}", created=now - timedelta(minutes=90, seconds=i))
    for i in range(60):
        fake.add_order(
            show_id=older["id"],
            buyer=f"old{i}",
            items=[{"title": f"Lot #{i}", "price_cents": 500}],
            created=now - timedelta(days=1, hours=1, seconds=i),
        )
    with app.app_context():
        client = client_for(shop["org"])
        sync.sync_shows(shop["org"], client)
        db.session.commit()
        shows = db.session.scalars(select(WhatnotShow).order_by(WhatnotShow.id)).all()
        assert len(shows) == 2
        before = len(fake.calls)
        sync.sync_orders_of(shop["org"], client, list(shows))
        db.session.commit()
        pages = [p for m, p in fake.calls[before:] if p.endswith("/orders") or "/orders?" in p]
        # One newest-first read down to the older show: 120 orders, 50 a page, plus the page
        # that crosses the older show's start. Read one by one, the newer show's pages would
        # have been read twice.
        assert len(pages) <= 4, pages
        assert db.session.query(WhatnotOrder).count() == 120
        assert all(ws.orders_synced_at is not None for ws in shows)


def test_a_choose_any_lot_listing_is_not_read_during_the_show_but_a_product_listing_is(app, shop):
    fake = shop["fake"]
    lot = fake.add_product(title="CHOOSE ANY PRODUCT YOU WANT #9", variants=[{"price_cents": 100}])
    bundle = fake.add_product(title="Red bundle", variants=[{"price_cents": 2500}])
    lot_variant = fake.variants_of(lot["id"])[0]
    bundle_variant = fake.variants_of(bundle["id"])[0]
    fake.add_order(
        show_id=shop["wshow"],
        buyer="ann",
        items=[
            {
                "title": "CHOOSE ANY PRODUCT YOU WANT #9",
                "product_id": lot["id"],
                "variant_id": lot_variant["id"],
            }
        ],
    )
    fake.add_order(
        show_id=shop["wshow"],
        buyer="bob",
        items=[
            {
                "title": "Red bundle",
                "product_id": bundle["id"],
                "variant_id": bundle_variant["id"],
            }
        ],
    )
    read(app, shop)
    with app.app_context():
        client = client_for(shop["org"])
        before = len(fake.calls)
        stored = listings.learn_listings(shop["org"], client, [shop["wshow"]])
        db.session.commit()
        asked = [p for _, p in fake.calls[before:]]
    assert stored == 1
    assert any(bundle["id"] in p for p in asked)
    assert not any(lot["id"] in p for p in asked)


def test_a_labelled_parcels_items_are_not_asked_for_again(app, shop):
    fake = shop["fake"]
    o = order(shop, "ann", "Red bundle")
    read(app, shop)
    parcel = fake.add_shipment(recipient="ann", order_ids=[o["id"]], status="label_generated")
    since = datetime.now(UTC) - timedelta(days=1)
    with app.app_context():
        client = client_for(shop["org"])
        shipping.sync_recent_shipments(shop["org"], client, since)
        db.session.commit()
        fake.set_shipment(parcel["id"], status="in_transit", tracking_code="9400111")
        before = len(fake.calls)
        shipping.sync_recent_shipments(shop["org"], client, since)
        db.session.commit()
        asked = [p for _, p in fake.calls[before:]]
        row = db.session.scalar(
            select(WhatnotShipment).where(WhatnotShipment.whatnot_id == parcel["id"])
        )
        assert row.api_status == "in_transit"  # the status is stored
        assert row.item_count == 1  # what was read with the label stands
    assert not any(p.endswith("/items") for p in asked), asked


def test_the_open_order_re_read_waits_while_a_show_is_on_air(app, shop):
    fake = shop["fake"]
    now = shop["now"]
    fake.add_show(title="On air", status="live", start=now - timedelta(minutes=30))
    with app.app_context():
        conn = db.session.scalar(
            select(WhatnotConnection).where(WhatnotConnection.organization_id == shop["org"])
        )
        conn.last_event_at = datetime.now(UTC)  # webhooks arriving: no live poll either
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        assert db.session.scalar(select(WhatnotShow).where(WhatnotShow.status == "live"))
        state = sync.connection.sync_state(shop["org"], "open_orders")
        assert state.last_started_at is None  # not read while the show is on air
    # Once it is over, the re-read runs (every few hours while webhooks arrive).
    with app.app_context():
        for ws in db.session.scalars(select(WhatnotShow).where(WhatnotShow.status == "live")):
            ws.status = "ended"
        for s in shop["fake"].shows.values():
            s["status"] = "ended"
        db.session.commit()
        sync.sync_job({"org_id": shop["org"]})
        db.session.commit()
        assert sync.connection.sync_state(shop["org"], "open_orders").last_started_at is not None

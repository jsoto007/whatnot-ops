"""Shipments, labels and carrier status from Whatnot: one package and one label per buyer.

Against the in-memory fake (Whatnot's published API description); never the live API. In
particular, that making a label closes the buyer's bundle is Whatnot's documented
behaviour, not something these tests observed.
"""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db import db
from app.models import (
    AuditLog,
    Order,
    Sale,
    Show,
    WhatnotConnection,
    WhatnotLot,
    WhatnotOrderItem,
    WhatnotShipment,
    WhatnotShow,
)
from app.services import activity, catalog, order_slips
from app.services.whatnot import client_for, connection, shipping, sync, webhooks
from tests import test_whatnot_orders as wo
from tests.test_pipeline import level, run
from tests.test_whatnot_orders import LOT, SECRET, apply, order, read, win

shop = wo.shop  # the same connected shop and matched show as the orders tests


@pytest.fixture
def pdfs(app):
    """Label downloads answer with a tiny PDF naming the link they came from."""
    fetched = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return b"%PDF-1.4 label from " + url.encode()

    app.config["WHATNOT_FETCH"] = fetch
    yield fetched
    app.config.pop("WHATNOT_FETCH", None)


def read_shipments(app, shop):
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"]))
        out = shipping.read_show(shop["org"], client_for(shop["org"]), ws)
        db.session.commit()
        return out


def view(app, shop):
    with app.app_context():
        v = shipping.packages(db.session.get(Show, shop["show"]))
        db.session.rollback()
        return v


def states(app, shop):
    return {b["buyer"]: b["state"] for b in view(app, shop)["buyers"]}


def parcel(shop, recipient, orders, **kw):
    kw.setdefault("created", shop["now"] - timedelta(minutes=50))
    return shop["fake"].add_shipment(recipient=recipient, order_ids=[o["id"] for o in orders], **kw)


def deliver(client, topic, data):
    raw = json.dumps(
        {
            "type": topic,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data,
            "version": "v2",
            "seller_id": "1234567",
        }
    ).encode()
    wid, ts = str(uuid.uuid4()), str(int(time.time()))
    headers = {
        "webhook-id": wid,
        "webhook-timestamp": ts,
        "webhook-signature": "v1," + webhooks.sign(SECRET, wid, ts, raw),
    }
    return client.post("/api/webhooks/whatnot", data=raw, headers=headers)


# ---------- reading ----------


def test_reading_shipments_keeps_status_and_tracking_but_never_the_label_or_an_address(app, shop):
    a, b = order(shop, "lani808", "Red bundle"), order(shop, "lani808", LOT, 700)
    s = parcel(shop, "lani808", [a, b], status="label_generated", tracking="9400111899")
    read(app, shop)
    assert read_shipments(app, shop) == {"seen": 1, "changed": 1}
    with app.app_context():
        row = db.session.scalar(select(WhatnotShipment))
        assert (row.whatnot_id, row.recipient, row.api_status, row.status) == (
            s["id"],
            "lani808",
            "label_generated",
            "Label ready",
        )
        assert (row.carrier, row.tracking_code, row.label_ready) == ("USPS", "9400111899", True)
        assert row.tracking == "USPS Ground Advantage 9400111899"
        assert (row.item_count, row.value_cents, row.show_id) == (2, 3200, shop["show"])
        assert row.url.startswith("https://www.whatnot.com/")
        # The label's link is nowhere: not on the shipment, not on its lots.
        kept = json.dumps(
            [{c.name: str(getattr(row, c.name)) for c in WhatnotShipment.__table__.columns}]
            + [
                {c.name: str(getattr(lot, c.name)) for c in WhatnotLot.__table__.columns}
                for lot in db.session.scalars(select(WhatnotLot))
            ]
        )
        assert "labels.example" not in kept and "label_url" not in kept
        lots = db.session.scalars(select(WhatnotLot).order_by(WhatnotLot.title)).all()
        assert [
            (lot.title, lot.whatnot_order_id, lot.price_cents, lot.sale_type) for lot in lots
        ] == [
            (LOT, b["id"], 700, "Auction"),
            ("Red bundle", a["id"], 2500, "Auction"),
        ]
        assert lots[0].lot_number == 73 and lots[0].shipping_cents == 525
        assert {i.whatnot_shipment_id for i in db.session.scalars(select(WhatnotOrderItem))} == {
            s["id"]
        }
    # A second read with nothing changed reads no shipment's items again.
    shop["fake"].calls.clear()
    assert read_shipments(app, shop) == {"seen": 1, "changed": 0}
    assert not [p for _, p in shop["fake"].calls if p.endswith("/items")]


def test_the_order_slips_become_the_pick_list_from_the_api_with_no_laptop_step(app, shop):
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    a, b = order(shop, "lani808", "Red bundle"), order(shop, "lani808", LOT, 700)
    parcel(shop, "lani808", [a, b])
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    with app.app_context():
        doc = order_slips.build(db.session.get(Show, shop["show"]))
    assert doc["whatnot"]["shipments"] == 1 and doc["whatnot"]["lots"] == 2
    assert doc["whatnot"]["lotsNotInApp"] == 0 and doc["whatnot"]["salesNotOnWhatnot"] == 0
    rows = doc["slips"][0]["rows"]
    assert [(r["product"], r["whatnotOrder"], r["onWhatnot"]) for r in rows] == [
        ("Red bundle", a["id"], True),
        ("No Pick", b["id"], True),
    ]
    assert doc["slips"][0]["whatnot"] == {"items": 2, "statuses": ["Needs label"]}


def test_a_cancelled_orders_lot_leaves_the_pick_list(app, shop):
    a, b = order(shop, "lani808", "Red bundle"), order(shop, "lani808", LOT, 700)
    parcel(shop, "lani808", [a, b])
    read(app, shop)
    read_shipments(app, shop)
    shop["fake"].set_order_status(b["id"], "cancelled")
    read(app, shop)
    with app.app_context():
        assert [lot.title for lot in db.session.scalars(select(WhatnotLot))] == ["Red bundle"]
    assert view(app, shop)["buyers"][0]["items"] == 1


def test_a_row_the_scraper_already_read_becomes_the_apis_row_not_a_second_one(app, shop):
    a = order(shop, "lani808", "Red bundle")
    s = parcel(shop, "lani808", [a])
    read(app, shop)
    with app.app_context():
        scraped = WhatnotShipment(
            organization_id=shop["org"],
            row_key="k" * 64,
            recipient="lani808",
            show_id=shop["show"],
            status="Ready to Ship",
        )
        db.session.add(scraped)
        db.session.flush()
        db.session.add(
            WhatnotLot(
                organization_id=shop["org"],
                show_id=shop["show"],
                shipment_id=scraped.id,
                whatnot_order_id=a["id"],
                recipient="lani808",
                title="Red bundle",
            )
        )
        db.session.commit()
    read_shipments(app, shop)
    with app.app_context():
        rows = db.session.scalars(select(WhatnotShipment)).all()
        assert len(rows) == 1 and rows[0].whatnot_id == s["id"] and rows[0].status == "Needs label"
        assert db.session.scalar(select(func.count()).select_from(WhatnotLot)) == 1


def test_without_the_customer_permission_the_buyer_comes_from_the_order(app, shop):
    a = order(shop, "lani808", "Red bundle")
    read(app, shop)
    parcel(shop, "lani808", [a], status="label_generated", tracking="94")
    shop["fake"].scopes = {"read:orders", "read:shows", "read:shipments"}
    read_shipments(app, shop)
    with app.app_context():
        row = db.session.scalar(select(WhatnotShipment))
        assert (row.recipient, row.label_ready) == ("lani808", True)
        with pytest.raises(shipping.ShippingError, match="permission to read customers"):
            shipping.label_pdf(row)


# ---------- one package per buyer ----------


def test_everything_a_buyer_won_in_the_show_is_one_package_ready_for_one_label(app, shop):
    wins = [
        order(shop, "lani808", "Red bundle"),
        order(shop, "lani808", LOT, 700),
        order(shop, "lani808", LOT, 900),
    ]
    parcel(shop, "lani808", wins)
    parcel(shop, "kat.c", [order(shop, "kat.c", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert [(b["buyer"], b["items"], len(b["packages"]), b["state"]) for b in v["buyers"]] == [
        ("lani808", 3, 1, "ready"),  # most items first, as on the order slips
        ("kat.c", 1, 1, "ready"),
    ]
    assert v["totals"] == {"buyers": 2, "ready": 2} and v["show"]["ended"] is True
    assert v["buyers"][0]["packages"][0]["otherItems"] == 0


def test_while_the_show_is_live_no_label_is_made(app, shop, sign_in, client):
    shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    assert states(app, shop) == {"lani808": "wait"}
    h = sign_in(shop["org"], role="manager")
    r = client.post(f"/api/shipping/shows/{shop['show']}/labels", json={"confirm": True}, headers=h)
    assert r.status_code == 409 and "closes the buyer's bundle" in r.get_json()["error"]
    assert shop["fake"].labels_requested == []


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_a_buyer_split_over_two_open_packages_is_called_out_and_gets_no_label(
    app, shop, sign_in, client
):
    first = parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")])
    parcel(shop, "lani808", [order(shop, "lani808", LOT, 700)])
    ok = parcel(shop, "kat.c", [order(shop, "kat.c", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    assert states(app, shop) == {"lani808": "split", "kat.c": "ready"}
    h = sign_in(shop["org"], role="manager")
    r = client.post(f"/api/shipping/shows/{shop['show']}/labels", json={"confirm": True}, headers=h)
    assert r.get_json()["requested"] == 1
    assert shop["fake"].labels_requested == [ok["id"]]  # never one of the split buyer's
    # Naming the split buyer changes nothing: two labels is what must not happen.
    r = client.post(
        f"/api/shipping/shows/{shop['show']}/labels",
        json={"confirm": True, "buyers": ["lani808"]},
        headers=h,
    )
    assert r.get_json()["requested"] == 0 and first["id"] not in shop["fake"].labels_requested


def test_a_package_whatnot_holds_back_is_not_labelled_and_says_why(app, shop):
    parcel(
        shop, "lani808", [order(shop, "lani808", "Red bundle")],
        required_actions=["order_pending_cancellation"],
    )  # fmt: skip
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert v["buyers"][0]["state"] == "hold"
    assert v["buyers"][0]["packages"][0]["holds"] == ["order_pending_cancellation"]


def test_a_win_not_in_any_package_yet_waits(app, shop):
    order(shop, "lani808", "Red bundle")
    read(app, shop)
    # The show's parcels have not been read since it ended: the parcel may just not be read
    # yet, so nobody is told to make one.
    v = view(app, shop)
    assert (v["buyers"][0]["state"], v["buyers"][0]["unplaced"], v["buyers"][0]["packages"]) == (
        "none",
        1,
        [],
    )


# ---------- a paid buyer Whatnot put in no parcel (Javier's call, 2026-11-19) ----------


def test_a_paid_buyer_whatnot_put_in_no_parcel_has_their_own_state_until_one_is_made(app, shop):
    lone = order(shop, "lani808", "Red bundle")
    parcel(shop, "kat.c", [order(shop, "kat.c", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)  # the show's final read: still no parcel for lani808
    v = view(app, shop)
    b = next(x for x in v["buyers"] if x["buyer"] == "lani808")
    assert (b["state"], b["unplaced"], b["packages"]) == ("no_parcel", 1, [])
    assert v["totals"] == {"buyers": 2, "no_parcel": 1, "ready": 1}
    assert v["labelsToMake"] == 1  # kat.c's; the app never makes a parcel or a label for lani808
    # Made in Seller Hub, then read again: the buyer is ready like any other.
    parcel(shop, "lani808", [lone])
    read_shipments(app, shop)
    assert states(app, shop) == {"lani808": "ready", "kat.c": "ready"}


def test_a_win_still_being_paid_for_is_not_called_a_buyer_with_no_parcel(app, shop):
    order(shop, "lani808", "Red bundle", status="pending")
    read(app, shop)
    read_shipments(app, shop)
    assert states(app, shop) == {"lani808": "none"}


def test_a_paid_item_left_out_of_the_buyers_parcel_is_called_out_and_gets_no_label(app, shop):
    first = order(shop, "lani808", "Red bundle")
    order(shop, "lani808", LOT, 700)  # in no parcel
    parcel(shop, "lani808", [first])
    read(app, shop)
    read_shipments(app, shop)
    b = view(app, shop)["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"], len(b["packages"])) == ("no_parcel", 2, 1, 1)
    # A label now would close the bundle without the item that is in no parcel.
    assert [p["askable"] for p in b["packages"]] == [False]


def test_a_paid_buyer_whose_only_parcel_was_cancelled_has_no_parcel(app, shop):
    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")], status="cancelled")
    read(app, shop)
    read_shipments(app, shop)
    b = view(app, shop)["buyers"][0]
    # Its item is in no parcel that ships, so it is counted as in none (the page then says
    # "this buyer", as for a buyer Whatnot never made a parcel for).
    assert (b["state"], b["items"], b["unplaced"]) == ("no_parcel", 1, 1)
    assert [p["status"] for p in b["packages"]] == ["cancelled"]


def cancelled_beside_open(app, shop, *, status="pending_fulfillment"):
    """lani808: a paid item in an open parcel, and a second item (its order `status`) whose
    only parcel Whatnot cancelled. Read in full after the show ended. → the open parcel."""
    first = order(shop, "lani808", "Red bundle")
    second = order(shop, "lani808", LOT, 700, status=status)
    parcel(shop, "lani808", [second], status="cancelled")
    s = parcel(shop, "lani808", [first])
    read(app, shop)
    read_shipments(app, shop)
    return s


def test_a_paid_item_whose_only_parcel_was_cancelled_keeps_the_open_parcel_from_a_label(app, shop):
    """One open parcel, and a paid item whose only parcel Whatnot cancelled: that item is in
    no parcel that ships, exactly as if it had none. A label for the open parcel would close
    the bundle without it, and a label cannot be undone (PR 80 verifier 6)."""
    cancelled_beside_open(app, shop)
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"]) == ("no_parcel", 2, 1)
    assert sorted((p["status"], p["items"], p["otherItems"]) for p in b["packages"]) == [
        ("cancelled", 1, 0),  # still shown, and its item is the buyer's own, not another show's
        ("created", 1, 0),
    ]
    assert v["labelsToMake"] == 0 and not any(p["askable"] for p in b["packages"])
    assert v["totals"] == {"buyers": 1, "no_parcel": 1}


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_no_label_is_sent_for_that_buyer_even_when_asked_for_by_name(app, client, shop, sign_in):
    """The server decides, not the page: naming the buyer, or pressing "Make labels", sends
    nothing."""
    cancelled_beside_open(app, shop)
    h = sign_in(shop["org"], role="manager")
    assert labels(client, shop, h, buyers=["lani808"]).get_json()["requested"] == 0
    assert labels(client, shop, h).get_json()["requested"] == 0
    assert shop["fake"].labels_requested == []


def test_a_label_job_that_names_that_buyers_open_parcel_sends_nothing(app, shop):
    """A job queued earlier (or by hand) for the open parcel looks at the buyer again first."""
    s = cancelled_beside_open(app, shop)
    with app.app_context():
        row = db.session.scalar(
            select(WhatnotShipment).where(WhatnotShipment.whatnot_id == s["id"])
        )
        payload = {"org_id": shop["org"], "shipment_ids": [row.id], "user_id": None}
        shipping.labels_job({**payload, "show_id": shop["show"]})
        assert db.session.get(WhatnotShipment, row.id).label_requested_at is None
    assert shop["fake"].labels_requested == []


def test_a_later_win_whose_only_parcel_was_cancelled_is_not_a_second_label(app, shop):
    """The other state a label is asked for in ("a later win needs its own label")."""
    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")], status="label_generated")
    parcel(shop, "lani808", [order(shop, "lani808", LOT, 700)], status="cancelled")
    parcel(shop, "lani808", [order(shop, "lani808", LOT, 900)])
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"]) == ("no_parcel", 3, 1)
    assert v["labelsToMake"] == 0


def test_a_parcel_cancelled_and_made_again_with_the_same_items_is_ready(app, shop):
    """Whatnot cancels a parcel and makes another holding its items (as the fake moves them:
    off the old one). The full read takes the newest first, so the cancelled one is read
    last: its items stay in the new parcel, and the buyer is ready for one label."""
    a = order(shop, "lani808", "Red bundle")
    old = parcel(shop, "lani808", [a])
    read(app, shop)
    read_shipments(app, shop)
    new = parcel(shop, "lani808", [a], created=shop["now"] - timedelta(minutes=10))
    shop["fake"].set_shipment(old["id"], status="cancelled", replacement_shipment_id=new["id"])
    read_shipments(app, shop)
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"]) == ("ready", 1, 0)
    assert [p["status"] for p in b["packages"]] == ["created"]
    assert v["labelsToMake"] == 1


def test_a_paid_item_whose_parcel_was_never_read_keeps_the_open_parcel_from_a_label(app, shop):
    """An item that names a parcel the app has no row for is in no parcel either."""
    cancelled_beside_open(app, shop)
    with app.app_context():
        item = db.session.scalar(select(WhatnotOrderItem).where(WhatnotOrderItem.title == LOT))
        item.whatnot_shipment_id = "4040404040"
        db.session.commit()
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"]) == ("no_parcel", 2, 1)
    assert v["labelsToMake"] == 0


@pytest.mark.parametrize("state", ["hold", "pickup", "split"])
def test_a_hold_a_pickup_or_a_split_still_comes_first_and_gets_no_label(app, shop, state):
    """The order of the states is unchanged: what is said about the open parcel(s) wins over
    the item in a cancelled one, and none of them is offered a label."""
    s = cancelled_beside_open(app, shop)
    if state == "hold":
        shop["fake"].set_shipment(s["id"], required_actions=["order_pending_cancellation"])
    elif state == "pickup":
        shop["fake"].set_shipment(s["id"], fulfillment_method="pickup")
    else:
        parcel(shop, "lani808", [order(shop, "lani808", LOT, 900)])
        read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["unplaced"]) == (state, 1)
    assert v["labelsToMake"] == 0 and not any(p["askable"] for p in b["packages"])


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_an_item_of_a_cancelled_or_failed_order_in_a_cancelled_parcel_holds_nothing_back(
    app, shop, status
):
    """Not a paid item: nothing to pack, so the buyer's open parcel is ready as before."""
    cancelled_beside_open(app, shop, status=status)
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"]) == ("ready", 1, 0)
    assert v["labelsToMake"] == 1


def test_an_item_still_being_paid_for_in_a_cancelled_parcel_is_not_called_no_parcel(app, shop):
    """As with such an item in no parcel at all: "not in a package yet", and no label while it
    may still be paid and need the bundle."""
    cancelled_beside_open(app, shop, status="pending")
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"]) == ("none", 2, 1)
    assert v["labelsToMake"] == 0


@pytest.mark.parametrize(
    "left, gone",
    [
        ("dropped_off", "cancelled"),
        # Whatnot cancelled it because its tracking stuck: it may have left all the same, so
        # the app cannot say either way. Still never marked shipped by the other parcel.
        ("delivered", "cancelled_tracking_stuck"),
    ],
)
@pytest.mark.parametrize("final_read", [True, False])
def test_a_buyer_whose_parcel_left_without_a_paid_item_is_not_marked_shipped(
    app, shop, left, gone, final_read
):
    """Every parcel that ships is with the carrier, but a paid item's only parcel was
    cancelled: the buyer is not "with the carrier" and their order is not marked shipped,
    before the show's final read as after it (PR 80 verifier probe p6)."""
    with app.app_context():
        catalog.create_product(
            shop["org"], "Blue bundle", skus=[{"code": "BLUE", "priceCents": 900}]
        )
        db.session.commit()
    shop["say"](app, [win("lani808", "Red bundle"), win("lani808", "Blue bundle", "9.00")])
    run(app, shop["show"])
    parcel(
        shop, "lani808", [order(shop, "lani808", "Red bundle")],
        status=left, tracking="9400AAA",
    )  # fmt: skip
    parcel(shop, "lani808", [order(shop, "lani808", "Blue bundle", 900)], status=gone)
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    if not final_read:
        not_read_in_full(app, shop)
    with app.app_context():  # one order in the app, both its wins named and paid
        o = db.session.scalar(select(Order).where(Order.buyer_username == "lani808"))
        assert (o.status.value, sorted(s.status.value for s in o.sales)) == ("paid", ["paid"] * 2)
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["unplaced"]) == ("no_parcel" if final_read else "none", 1)
    assert sorted(p["status"] for p in b["packages"]) == sorted([left, gone])
    assert v["toMarkShipped"] == 0 and v["labelsToMake"] == 0
    with app.app_context():
        assert shipping.mark_shipped(db.session.get(Show, shop["show"])) == 0
        db.session.commit()
        o = db.session.scalar(select(Order).where(Order.buyer_username == "lani808"))
        assert o.status.value == "paid"


def not_read_in_full(app, shop):
    """As if the parcels were last read while the show was still live."""
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow).where(WhatnotShow.whatnot_id == shop["wshow"]))
        connection.sync_state(shop["org"], f"shipments:{ws.id}").last_ok_at = (
            ws.ended_at - timedelta(minutes=1)
        )
        db.session.commit()


@pytest.mark.parametrize("state", ["wait", "shared", "reading"])
def test_before_the_final_read_an_item_in_a_cancelled_parcel_leaves_the_state_as_it_was(
    app, shop, state
):
    """Until the show was read in full after it ended, a buyer with an open parcel keeps the
    state that says why nobody is ready yet ("no_parcel" only after that read). No label
    either way (PR 80 verifier probe p4)."""
    if state == "wait":
        shop["fake"].shows[shop["wshow"]].update(status="live", ended_at=None)
    first = order(shop, "lani808", "Red bundle")
    second = order(shop, "lani808", LOT, 700)
    parcel(shop, "lani808", [second], status="cancelled")
    in_open = [first]
    if state == "shared":
        live = shop["fake"].add_show(
            title="Saturday", status="live", start=shop["now"] - timedelta(minutes=4)
        )
        in_open.append(
            shop["fake"].add_order(
                show_id=live["id"],
                buyer="lani808",
                items=[{"title": "Red bundle", "price_cents": 2500}],
                created=shop["now"] - timedelta(minutes=2),
            )
        )
    parcel(shop, "lani808", in_open)
    read(app, shop)
    read_shipments(app, shop)
    if state == "reading":
        not_read_in_full(app, shop)
    v = view(app, shop)
    b = v["buyers"][0]
    assert (b["state"], b["items"], b["unplaced"]) == (state, 2, 1)
    assert v["labelsToMake"] == 0 and not any(p["askable"] for p in b["packages"])


# ---------- a parcel that does not ship never takes an item from one that does ----------


def still_lists(fake, shipment_id, order_id):
    """Whatnot's cancelled parcel still lists this order's items, beside the parcel that now
    holds them (the fake moves items off the old parcel; Whatnot may not)."""
    now = fake.tick()
    for item in fake.items_of(order_id):
        sid = fake._next_id()
        fake.shipment_items[sid] = {
            "id": sid,
            "shipment_id": shipment_id,
            "order_id": order_id,
            "order_item_id": item["id"],
            "dimension_profile": None,
            "hazardous_material": "not_hazardous",
            "country_of_origin": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }


def replaced(shop, *, cancelled_first=True):
    """lani808's parcel was cancelled and made again (the new one holds both wins). The
    cancelled one still lists the first win."""
    a = order(shop, "lani808", "Red bundle")
    b = order(shop, "lani808", LOT, 700)
    old_at, new_at = (55, 45) if cancelled_first else (45, 55)
    old = parcel(shop, "lani808", [a], created=shop["now"] - timedelta(minutes=old_at))
    new = parcel(shop, "lani808", [a, b], created=shop["now"] - timedelta(minutes=new_at))
    shop["fake"].set_shipment(old["id"], status="cancelled")
    still_lists(shop["fake"], old["id"], a["id"])
    return a, old, new


def summary(v):
    b = v["buyers"][0]
    return (
        b["state"], b["items"], b["unplaced"],
        sorted((p["status"], p["items"], p["askable"]) for p in b["packages"]),
        v["labelsToMake"],
    )  # fmt: skip


READY_IN_THE_NEW_PARCEL = ("ready", 2, 0, [("created", 2, True)], 1)


@pytest.mark.parametrize("cancelled_first", [True, False])
@pytest.mark.parametrize("touched", [False, True])
def test_a_cancelled_parcel_still_listing_an_item_never_takes_it_from_the_open_one(
    app, shop, cancelled_first, touched
):
    """The full read takes the newest parcel first, so the cancelled one may be read last:
    whatever the order, the item stays in the open parcel, and every read again agrees. HEAD
    offered this label; round 1 called the buyer "no parcel" (PR 80 verifier probe p7)."""
    _, _, new = replaced(shop, cancelled_first=cancelled_first)
    read(app, shop)
    seen = []
    for _ in range(2):  # the second: "Read again from Whatnot"
        read_shipments(app, shop)
        seen.append(summary(view(app, shop)))
    if touched:
        shop["fake"].set_shipment(new["id"])  # Whatnot changes the open parcel
        read_shipments(app, shop)
        seen.append(summary(view(app, shop)))
    assert seen == [READY_IN_THE_NEW_PARCEL] * len(seen)


def test_reading_the_cancelled_parcel_alone_later_leaves_the_item_in_the_open_one(app, shop):
    """A webhook reads one parcel at a time, in any order."""
    a, old, new = replaced(shop)
    read(app, shop)
    read_shipments(app, shop)
    assert summary(view(app, shop)) == READY_IN_THE_NEW_PARCEL
    with app.app_context():
        shipping.sync_shipment(shop["org"], client_for(shop["org"]), old["id"])
        db.session.commit()
        item = db.session.scalar(
            select(WhatnotOrderItem).where(WhatnotOrderItem.title == "Red bundle")
        )
        lot = db.session.scalar(select(WhatnotLot).where(WhatnotLot.whatnot_order_id == a["id"]))
        # The item and its lot on the order slips both stay with the parcel that ships.
        assert item.whatnot_shipment_id == new["id"]
        assert db.session.get(WhatnotShipment, lot.shipment_id).whatnot_id == new["id"]
    assert summary(view(app, shop)) == READY_IN_THE_NEW_PARCEL


def test_an_order_read_again_naming_the_cancelled_parcel_is_safe_then_mended(app, shop):
    """The other writer of an item's parcel: Whatnot's own order item, read again. It has no
    way to ask the open parcel whether it still lists the item, so it writes what Whatnot
    says: the buyer gets no label meanwhile (safe), and the next read of the parcels reads
    the open one again and puts the item back."""
    a, old, new = replaced(shop)
    for item in shop["fake"].items_of(a["id"]):
        item["shipment_id"] = old["id"]
    read(app, shop)
    read_shipments(app, shop)
    assert summary(view(app, shop)) == READY_IN_THE_NEW_PARCEL
    with app.app_context():
        sync.sync_order(shop["org"], client_for(shop["org"]), a["id"])
        db.session.commit()
    v = view(app, shop)
    assert (summary(v)[0], v["labelsToMake"]) == ("no_parcel", 0)
    read_shipments(app, shop)
    assert summary(view(app, shop)) == READY_IN_THE_NEW_PARCEL


def strand(app, shop, title, whatnot_id):
    """Point an item at a parcel directly: rows written before this rule, or by a webhook
    that raced the full read."""
    with app.app_context():
        item = db.session.scalar(select(WhatnotOrderItem).where(WhatnotOrderItem.title == title))
        item.whatnot_shipment_id = whatnot_id
        db.session.commit()


def items_read(shop):
    return [p for _, p in shop["fake"].calls if p.endswith("/items")]


def test_read_again_mends_an_item_left_on_a_cancelled_parcel_the_open_one_lists(app, shop):
    """Whatnot changed nothing, so the full read would skip the open parcel: it is read
    again because its buyer has an item on a parcel that does not ship, and the item goes
    back to the parcel that lists it."""
    _, old, new = replaced(shop)
    read(app, shop)
    read_shipments(app, shop)
    strand(app, shop, "Red bundle", old["id"])
    assert summary(view(app, shop))[0] == "no_parcel"
    shop["fake"].calls.clear()
    read_shipments(app, shop)  # "Read again from Whatnot"
    assert summary(view(app, shop)) == READY_IN_THE_NEW_PARCEL
    assert items_read(shop) == [f"/shipments/{new['id']}/items"]
    shop["fake"].calls.clear()
    read_shipments(app, shop)  # mended: nothing is read again
    assert items_read(shop) == []


@pytest.mark.parametrize("status", ["label_generated", "in_transit", "delivered"])
def test_read_again_mends_an_item_left_on_a_cancelled_parcel_a_labelled_one_lists(
    app, shop, status
):
    """The same stale row, but the parcel that lists the item already has its label or left:
    a row written before the rule must not show a buyer whose parcel shipped as "no parcel"
    for ever (PR 80 verifier probe p2dm). The labelled parcel takes the item back, and
    keeps everything it had."""
    _, old, new = replaced(shop)
    shop["fake"].set_shipment(new["id"], status=status)
    read(app, shop)
    read_shipments(app, shop)
    mended = summary(view(app, shop))
    assert mended[0] in ("labelled", "shipped") and mended[1:3] == (2, 0)
    strand(app, shop, "Red bundle", old["id"])
    assert summary(view(app, shop))[0] == "no_parcel"
    shop["fake"].calls.clear()
    read_shipments(app, shop)  # "Read again from Whatnot"
    assert summary(view(app, shop)) == mended
    assert items_read(shop) == [f"/shipments/{new['id']}/items"]
    shop["fake"].calls.clear()
    read_shipments(app, shop)  # mended: nothing is read again
    assert items_read(shop) == []


def test_a_labelled_parcel_taking_an_item_back_drops_none_of_its_own(app, shop):
    """Whatnot may stop listing an item under a labelled parcel: the mend only adds."""
    a, old, new = replaced(shop)
    fake = shop["fake"]
    fake.set_shipment(new["id"], status="label_generated")
    read(app, shop)
    read_shipments(app, shop)
    strand(app, shop, "Red bundle", old["id"])
    for sid, si in list(fake.shipment_items.items()):  # the labelled parcel lists only Red now
        if si["shipment_id"] == new["id"] and si["order_id"] != a["id"]:
            del fake.shipment_items[sid]
    read_shipments(app, shop)
    assert summary(view(app, shop))[:4] == ("labelled", 2, 0, [("label_generated", 2, False)])


def test_an_item_an_open_parcel_and_a_delivered_one_both_list_goes_to_the_open_one(app, shop):
    """The open parcel is mended first and keeps the item: the delivered one must not take
    it after (that marked the order shipped while a parcel was still to label; PR 80
    review probe c1). The stranded buyer ends exactly as one who was never stranded."""
    a = order(shop, "lani808", "Red bundle")
    b = order(shop, "lani808", "Blue bundle", 900)
    can = parcel(
        shop, "lani808", [], status="cancelled", created=shop["now"] - timedelta(minutes=58)
    )
    late = parcel(
        shop, "lani808", [b], status="delivered", created=shop["now"] - timedelta(minutes=45)
    )
    parcel(shop, "lani808", [a])  # the open one is older: the full read takes it last
    still_lists(shop["fake"], late["id"], a["id"])  # Whatnot lists Red under both
    read(app, shop)
    read_shipments(app, shop)
    never_stranded = summary(view(app, shop))
    assert never_stranded[0] == "second"
    strand(app, shop, "Red bundle", can["id"])
    assert summary(view(app, shop))[0] == "no_parcel"
    read_shipments(app, shop)
    assert summary(view(app, shop)) == never_stranded
    assert view(app, shop)["toMarkShipped"] == 0


def lots(app):
    with app.app_context():
        return sorted(
            db.session.execute(select(WhatnotLot.whatnot_order_id, WhatnotLot.title)).all()
        )


def test_a_labelled_parcel_taking_one_item_of_an_order_back_overwrites_no_lot(app, shop):
    """Both items of one order were stored on the cancelled parcel, and Whatnot lists only
    one of them under the labelled parcel now: that one goes back, and the order's lots stay
    as they were (written by place in a list, the item taken back overwrote the other's;
    PR 80 review probe c2)."""
    fake = shop["fake"]
    m = fake.add_order(
        show_id=shop["wshow"],
        buyer="lani808",
        items=[{"title": "Red bundle", "price_cents": 2500}, {"title": LOT, "price_cents": 700}],
        created=datetime.now(UTC) - timedelta(seconds=20),
    )
    red, lot = (i["id"] for i in fake.items_of(m["id"]))
    can = parcel(
        shop, "lani808", [], status="cancelled", created=shop["now"] - timedelta(minutes=55)
    )
    lab = parcel(shop, "lani808", [m], status="label_generated")
    read(app, shop)
    read_shipments(app, shop)
    before = lots(app)
    assert len(before) == 2
    for sid, si in list(fake.shipment_items.items()):  # Whatnot lists only Red under it now
        if si["shipment_id"] == lab["id"] and si["order_item_id"] == lot:
            del fake.shipment_items[sid]
    strand(app, shop, "Red bundle", can["id"])
    strand(app, shop, LOT, can["id"])
    read_shipments(app, shop)
    assert lots(app) == before
    with app.app_context():
        where = dict(
            db.session.execute(
                select(WhatnotOrderItem.whatnot_id, WhatnotOrderItem.whatnot_shipment_id)
            ).all()
        )
    assert where == {red: lab["id"], lot: can["id"]}
    assert summary(view(app, shop))[0] == "no_parcel"  # the other item is still in none


def test_whatnot_failing_on_a_mend_does_not_fail_the_read(app, shop, monkeypatch):
    """The extra read of a labelled parcel is best effort: the parcels themselves were read."""
    _, old, new = replaced(shop)
    shop["fake"].set_shipment(new["id"], status="label_generated")
    read(app, shop)
    read_shipments(app, shop)
    strand(app, shop, "Red bundle", old["id"])

    def down(*args, **kwargs):
        raise shipping.WhatnotError("unavailable")

    monkeypatch.setattr(shipping, "_take_back", down)
    read_shipments(app, shop)  # does not raise
    assert summary(view(app, shop))[0] == "no_parcel"
    monkeypatch.undo()
    read_shipments(app, shop)
    assert summary(view(app, shop))[0] == "labelled"


@pytest.mark.parametrize("open_touched", [True, False])
def test_an_item_only_the_cancelled_parcel_lists_stays_there_and_holds_the_label_back(
    app, shop, open_touched
):
    """The guard takes nothing from a cancelled parcel that no parcel that ships lists: the
    item is in no parcel. Whatnot's time on the open parcel may not change when an item
    leaves it, so the stored pointer is not proof: the open parcel's list is asked for.
    While it lasts, the buyer's open parcel is read again at each read (one call), in case
    Whatnot puts the item back in it."""
    a, old, new = replaced(shop)
    read(app, shop)
    read_shipments(app, shop)
    assert summary(view(app, shop)) == READY_IN_THE_NEW_PARCEL
    # Whatnot takes the win out of the open parcel; only the cancelled one lists it now.
    fake = shop["fake"]
    for sid, si in list(fake.shipment_items.items()):
        if si["shipment_id"] == new["id"] and si["order_id"] == a["id"]:
            del fake.shipment_items[sid]
    if open_touched:
        fake.set_shipment(new["id"])
    fake.set_shipment(old["id"])
    read_shipments(app, shop)
    want = ("no_parcel", 2, 1, [("cancelled", 1, False), ("created", 1, False)], 0)
    assert summary(view(app, shop)) == want
    fake.calls.clear()
    read_shipments(app, shop)
    assert summary(view(app, shop)) == want
    assert items_read(shop) == [f"/shipments/{new['id']}/items"]


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_a_win_whatnot_moved_into_a_cancelled_parcel_is_not_kept_in_the_open_one(
    app, client, shop, sign_in
):
    """Whatnot moves a paid win out of the open parcel into one it cancelled, and the open
    parcel's own time does not change (the fake does exactly that), so the full read skips
    it. The win is in no parcel that ships: no label for the open parcel (the round-1 fix)."""
    first = order(shop, "lani808", "Red bundle")
    second = order(shop, "lani808", LOT, 700)
    parcel(shop, "lani808", [first, second])
    read(app, shop)
    read_shipments(app, shop)
    assert summary(view(app, shop)) == READY_IN_THE_NEW_PARCEL
    parcel(
        shop, "lani808", [second], status="cancelled", created=shop["now"] - timedelta(minutes=10)
    )
    read_shipments(app, shop)
    v = view(app, shop)
    assert summary(v) == ("no_parcel", 2, 1, [("cancelled", 1, False), ("created", 1, False)], 0)
    h = sign_in(shop["org"], role="manager")
    assert labels(client, shop, h).get_json()["requested"] == 0
    assert shop["fake"].labels_requested == []


def test_two_open_parcels_are_still_split_with_an_item_in_no_parcel(app, shop):
    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")])
    parcel(shop, "lani808", [order(shop, "lani808", LOT, 700)])
    order(shop, "lani808", LOT, 900)  # in no parcel
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert v["buyers"][0]["state"] == "split"
    assert v["labelsToMake"] == 0 and not any(p["askable"] for p in v["buyers"][0]["packages"])


def test_a_parcel_that_also_holds_another_shows_wins_says_so(app, shop):
    other = shop["fake"].add_show(
        title="Thursday",
        start=shop["now"] - timedelta(days=1, hours=2),
        ended=shop["now"] - timedelta(days=1),
    )
    earlier = shop["fake"].add_order(
        show_id=other["id"], buyer="lani808", items=[{"title": "Red bundle", "price_cents": 2500}],
        created=shop["now"] - timedelta(days=1, hours=1),
    )  # fmt: skip
    parcel(shop, "lani808", [earlier, order(shop, "lani808", LOT, 700)])
    read(app, shop)
    read_shipments(app, shop)
    p = view(app, shop)["buyers"][0]["packages"][0]
    assert (p["items"], p["otherItems"]) == (1, 1)


# ---------- labels ----------


def labels(client, shop, h, **body):
    return client.post(
        f"/api/shipping/shows/{shop['show']}/labels", json={"confirm": True, **body}, headers=h
    )


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_labels_are_asked_for_once_per_package_and_arrive_later(app, client, shop, sign_in):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    s = parcel(
        shop, "lani808", [order(shop, "lani808", "Red bundle"), order(shop, "lani808", LOT, 700)]
    )
    read(app, shop)
    read_shipments(app, shop)
    h = sign_in(shop["org"], role="manager")
    url = f"/api/shipping/shows/{shop['show']}/labels"
    assert client.post(url, json={}, headers=h).status_code == 400  # the page must confirm first
    assert shop["fake"].labels_requested == []
    r = labels(client, shop, h)
    assert r.get_json()["requested"] == 1 and shop["fake"].labels_requested == [s["id"]]
    pack = r.get_json()["buyers"][0]["packages"][0]
    assert pack["labelRequestedAt"] and pack["status"] == "created" and pack["labelProblem"] is None
    # A second press while Whatnot is still making it asks for nothing.
    assert labels(client, shop, h).get_json()["requested"] == 0
    assert shop["fake"].labels_requested == [s["id"]]
    with app.app_context():
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "labels.requested"))
        assert (
            activity.describe(entry.action, entry.details)
            == "Asked Whatnot to make 1 shipping label"
        )
    # Whatnot finishes and says so.
    shop["fake"].finish_label(s["id"])
    assert deliver(client, "shipments.label_generated", {"shipment_id": s["id"]}).status_code == 204
    v = view(app, shop)
    assert (
        v["buyers"][0]["state"] == "labelled"
        and v["buyers"][0]["packages"][0]["labelReady"] is True
    )
    assert v["buyers"][0]["packages"][0]["tracking"] == "9400111899223344556677"


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_a_refused_label_says_why_and_the_others_still_go(app, client, shop, sign_in):
    a = parcel(
        shop,
        "a_buyer",
        [
            order(shop, "a_buyer", "Red bundle"),
            order(shop, "a_buyer", LOT),
            order(shop, "a_buyer", LOT),
        ],
    )
    b = parcel(shop, "b_buyer", [order(shop, "b_buyer", "Red bundle"), order(shop, "b_buyer", LOT)])
    c = parcel(shop, "c_buyer", [order(shop, "c_buyer", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    h = sign_in(shop["org"], role="manager")
    shop["fake"].fail_next(403, "seller-verification-required")
    shop["fake"].fail_next(403, "missing-scope", required_scopes=["write:shipments"])
    got = {
        x["buyer"]: x["packages"][0]["labelProblem"]
        for x in labels(client, shop, h).get_json()["buyers"]
    }
    assert got == {"a_buyer": "needs_verification", "b_buyer": "no_permission", "c_buyer": None}
    assert shop["fake"].labels_requested == [c["id"]] and a["id"] != b["id"]
    with app.app_context():
        # A missing write permission is about labels only: the connection itself is fine.
        assert db.session.scalar(select(WhatnotConnection)).problem is None
    # Refused ones can be asked for again at once (the block is only for requests in flight).
    assert labels(client, shop, h).get_json()["requested"] == 2


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_a_later_win_after_the_label_is_a_second_package_with_its_own_label(
    app, client, shop, sign_in
):
    first = parcel(
        shop,
        "lani808",
        [order(shop, "lani808", "Red bundle")],
        status="label_generated",
        tracking="94",
    )
    second = parcel(shop, "lani808", [order(shop, "lani808", LOT, 700)])
    read(app, shop)
    read_shipments(app, shop)
    assert states(app, shop) == {"lani808": "second"}
    h = sign_in(shop["org"], role="manager")
    assert labels(client, shop, h).get_json()["requested"] == 1
    assert shop["fake"].labels_requested == [second["id"]] and first["id"] != second["id"]


def test_printing_streams_whatnots_label_and_slip_and_keeps_nothing(
    app, client, shop, sign_in, pdfs
):
    s = parcel(
        shop,
        "lani808",
        [order(shop, "lani808", "Red bundle")],
        status="label_generated",
        tracking="94",
    )
    read(app, shop)
    read_shipments(app, shop)
    sign_in(shop["org"], role="fulfillment")
    with app.app_context():
        row_id = db.session.scalar(select(WhatnotShipment.id))
    r = client.get(f"/api/shipping/shipments/{row_id}/label.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")
    assert r.headers["Cache-Control"] == "no-store" and "inline" in r.headers["Content-Disposition"]
    assert pdfs == [f"https://labels.example/{s['id']}-bundle.pdf"]  # label + slip, fetched fresh
    with app.app_context():
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "label.printed"))
        assert entry.details is None and "labels.example" not in json.dumps(
            [str(v) for v in vars(db.session.scalar(select(WhatnotShipment))).values()]
        )


def test_a_label_that_is_not_there_or_not_a_pdf_is_said_in_words(app, client, shop, sign_in, pdfs):
    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    sign_in(shop["org"], role="fulfillment")
    with app.app_context():
        row_id = db.session.scalar(select(WhatnotShipment.id))
    r = client.get(f"/api/shipping/shipments/{row_id}/label.pdf")
    assert r.status_code == 409 and r.get_json()["error"] == "This package has no label yet."
    shop["fake"].finish_label(next(iter(shop["fake"].shipments)))
    app.config["WHATNOT_FETCH"] = lambda url: b"<html>sign in</html>"
    r = client.get(f"/api/shipping/shipments/{row_id}/label.pdf")
    assert r.status_code == 409 and "not a label" in r.get_json()["error"]
    assert client.get("/api/shipping/shipments/999999/label.pdf").status_code == 404


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://labels.example/a.pdf", True),
        ("http://labels.example/a.pdf", False),
        ("https://localhost/a.pdf", False),
        ("https://127.0.0.1/a.pdf", False),
        ("https://10.0.0.8/a.pdf", False),
        ("https://169.254.169.254/latest/meta-data", False),
        ("https://printer.local/a.pdf", False),
        ("file:///etc/passwd", False),
        ("javascript:alert(1)", False),
        (None, False),
    ],
)
def test_only_a_plain_https_link_to_a_public_host_is_ever_fetched(url, ok):
    assert (shipping._safe_url(url) is not None) is ok


# ---------- the carrier has it ----------


def test_when_the_carrier_has_every_package_a_person_can_mark_the_orders_shipped(
    app, client, shop, sign_in
):
    shop["say"](app, [win("lani808", "Red bundle"), win("kat.c", "Red bundle")])
    run(app, shop["show"])
    a, b = order(shop, "lani808", "Red bundle"), order(shop, "kat.c", "Red bundle")
    sa = parcel(shop, "lani808", [a], status="dropped_off", tracking="9400AAA")
    parcel(shop, "kat.c", [b], status="label_generated", tracking="9400BBB")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert (v["toMarkShipped"], states(app, shop)) == (
        1,
        {"lani808": "shipped", "kat.c": "labelled"},
    )
    with app.app_context():  # shadow mode: reading changed no order
        assert {o.buyer_username: o.status.value for o in db.session.scalars(select(Order))} == {
            "lani808": "paid",
            "kat.c": "paid",
        }
    h = sign_in(shop["org"], role="fulfillment")
    r = client.post(f"/api/shipping/shows/{shop['show']}/mark-shipped", headers=h).get_json()
    assert r["marked"] == 1 and r["toMarkShipped"] == 0
    with app.app_context():
        o = db.session.scalar(select(Order).where(Order.buyer_username == "lani808"))
        assert (
            (o.status.value, o.tracking) == ("shipped", "9400AAA") and o.shipped_at and o.picked_at
        )
        assert [s.status.value for s in o.sales] == ["fulfilled"]
        assert (
            db.session.scalar(select(Order).where(Order.buyer_username == "kat.c")).status.value
            == "paid"
        )
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "whatnot.orders_shipped")
        )
        assert activity.describe(entry.action, entry.details) == (
            "Marked 1 order shipped: the carrier has them"
        )
    assert level(app, shop["red"]) == (1, 0)  # shipping moves no stock
    assert sa["id"]


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_in_auto_mode_carrier_news_marks_the_order_shipped_on_its_own(app, client, shop):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    with app.app_context():
        db.session.scalar(select(WhatnotConnection)).orders_mode = "auto"
        db.session.commit()
    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    a, b = order(shop, "lani808", "Red bundle"), order(shop, "lani808", LOT, 700)
    s1 = parcel(shop, "lani808", [a], status="label_generated", tracking="9400AAA")
    s2 = parcel(shop, "lani808", [b], status="label_generated", tracking="9400BBB")
    read(app, shop)
    apply(app, shop)
    with app.app_context():  # name the second lot so the order is fully paid
        db.session.scalar(select(Sale).where(Sale.item_text == LOT)).status  # noqa: B018
    read_shipments(app, shop)
    shop["fake"].set_shipment(s1["id"], status="dropped_off")
    deliver(client, "shipments.tracking_code_added", {"shipment_id": s1["id"]})
    with app.app_context():  # one of two packages: not shipped yet
        assert db.session.scalar(select(Order)).status.value != "shipped"
        lot_sale = db.session.scalar(select(Sale).where(Sale.item_text == LOT))
        from app.models import ReviewItem, User
        from app.services import pipeline

        item = db.session.scalar(select(ReviewItem).where(ReviewItem.sale_id == lot_sale.id))
        user = User(
            organization_id=shop["org"],
            email="m@x.test",
            name="M",
            password_hash="x",
            role="manager",
            session_token="t",
        )
        db.session.add(user)
        db.session.flush()
        pipeline.resolve(item, "assign_sku", {"skuId": shop["red"]}, user=user)
        db.session.commit()
    shop["fake"].set_shipment(s2["id"], status="in_transit")
    deliver(client, "shipments.tracking_code_added", {"shipment_id": s2["id"]})
    with app.app_context():
        o = db.session.scalar(select(Order))
        assert o.status.value == "shipped" and {s.status.value for s in o.sales} == {"fulfilled"}
        entry = db.session.scalar(
            select(AuditLog).where(AuditLog.action == "whatnot.orders_shipped")
        )
        assert activity.who(entry.action, entry.actor_type, entry.actor_label) == "Whatnot"


def test_an_order_with_an_item_still_in_review_is_not_marked_shipped(app, shop):
    a = order(shop, "newbie", LOT, 700)
    parcel(shop, "newbie", [a], status="dropped_off", tracking="94")
    read(app, shop)
    apply(app, shop)  # a sale with no product yet: the order needs review
    read_shipments(app, shop)
    assert view(app, shop)["toMarkShipped"] == 0
    with app.app_context():
        assert shipping.mark_shipped(db.session.get(Show, shop["show"])) == 0
        assert db.session.scalar(select(Order)).status.value == "open"


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_a_replaced_shipment_is_followed_to_the_one_that_has_its_items(app, client, shop):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    a = order(shop, "lani808", "Red bundle")
    old = parcel(shop, "lani808", [a])
    read(app, shop)
    read_shipments(app, shop)
    new = parcel(shop, "lani808", [a], status="label_generated", tracking="9400NEW")  # items move
    shop["fake"].set_shipment(old["id"], status="cancelled", replacement_shipment_id=new["id"])
    body = {"previous_shipment": {"id": old["id"]}, "replacement_shipment": {"id": new["id"]}}
    assert deliver(client, "shipments.replaced", body).status_code == 204
    v = view(app, shop)
    assert [p["tracking"] for p in v["buyers"][0]["packages"]] == ["9400NEW"]
    assert v["buyers"][0]["state"] == "labelled"
    with app.app_context():
        rows = {r.whatnot_id: r for r in db.session.scalars(select(WhatnotShipment))}
        assert rows[old["id"]].status == "Cancelled" and rows[old["id"]].replaced_by == new["id"]


# ---------- routes, people, the timer ----------


def test_who_may_look_print_mark_shipped_and_make_labels(app, client, shop, sign_in):
    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    base = f"/api/shipping/shows/{shop['show']}"
    h = sign_in(shop["org"], role="viewer")
    assert client.get(base).status_code == 200
    for path in ("/refresh", "/mark-shipped", "/labels"):
        assert client.post(base + path, json={"confirm": True}, headers=h).status_code == 403
    with app.app_context():
        row_id = db.session.scalar(select(WhatnotShipment.id))
    assert client.get(f"/api/shipping/shipments/{row_id}/label.pdf").status_code == 403
    client.post("/api/auth/logout", headers=h)
    h = sign_in(shop["org"], role="fulfillment", email="p@x.test")
    assert client.post(base + "/refresh", headers=h).status_code == 200
    assert client.post(base + "/labels", json={"confirm": True}, headers=h).status_code == 403
    assert shop["fake"].labels_requested == []
    assert client.post(base + "/mark-shipped").status_code == 403  # no CSRF token


def test_another_shop_sees_none_of_it(app, client, shop, sign_in, pdfs):
    from app.models import Organization

    parcel(
        shop,
        "lani808",
        [order(shop, "lani808", "Red bundle")],
        status="label_generated",
        tracking="94",
    )
    read(app, shop)
    read_shipments(app, shop)
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id, row_id = other.id, db.session.scalar(select(WhatnotShipment.id))
    h = sign_in(other_id)
    assert client.get(f"/api/shipping/shows/{shop['show']}").status_code == 404
    assert (
        client.post(
            f"/api/shipping/shows/{shop['show']}/labels", json={"confirm": True}, headers=h
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/shipping/shipments/{row_id}/label.pdf").status_code == 404 and pdfs == []
    )


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_refresh_reads_again_and_a_show_ending_reads_its_shipments(app, client, shop, sign_in):
    app.config["WHATNOT_WEBHOOK_SECRET"] = SECRET
    a = order(shop, "lani808", "Red bundle")
    parcel(shop, "lani808", [a])
    assert deliver(client, "shows.ended", {"show_id": shop["wshow"]}).status_code == 204
    assert states(app, shop) == {"lani808": "ready"}  # orders, then the parcels they are in
    shop["fake"].finish_label(next(iter(shop["fake"].shipments)))
    h = sign_in(shop["org"], role="fulfillment")
    r = client.post(f"/api/shipping/shows/{shop['show']}/refresh", headers=h).get_json()
    assert r["queued"] is False and r["buyers"][0]["state"] == "labelled"


def test_the_timer_looks_at_shipments_only_while_some_are_on_their_way(app, shop):
    with app.app_context():
        read(app, shop)  # a show that ended minutes ago: its labels are still to make
        assert shipping.due(shop["org"]) is True
        shipping.sync._run(shop["org"], "shipments", lambda: {})
        db.session.commit()
        assert shipping.due(shop["org"]) is False  # just looked
        ws = db.session.scalar(select(WhatnotShow))
        ws.ended_at = datetime.now(UTC) - timedelta(days=10)
        from app.models import WhatnotSyncState

        db.session.query(WhatnotSyncState).update(
            {"last_started_at": datetime.now(UTC) - timedelta(hours=2)}
        )
        db.session.commit()
        assert shipping.due(shop["org"]) is False  # nothing recent, nothing moving


# ---------- regressions from the 2026-11-10 money-safety review ----------


def ready(app, shop, buyer="lani808"):
    s = parcel(shop, buyer, [order(shop, buyer, "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    return s


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_two_people_pressing_make_labels_together_ask_whatnot_once(app, shop):
    """A label cannot be undone. Both presses used to pass the "already asked?" check and
    each sent its own request."""
    import threading
    from types import SimpleNamespace

    s = ready(app, shop)
    gate = threading.Barrier(2, timeout=20)
    real = shipping.packages

    def together(show):
        view = real(show)
        gate.wait()  # both have read "nobody asked yet" before either goes on
        return view

    shipping.packages = together
    out = []

    def press():
        with app.app_context():
            result = shipping.request_labels(
                db.session.get(Show, shop["show"]), user=SimpleNamespace(id=None)
            )
            db.session.commit()
            out.append(result["requested"])

    try:
        threads = [threading.Thread(target=press) for _ in range(2)]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
    finally:
        shipping.packages = real
    assert out == [1, 1]  # each was told its request went in
    assert shop["fake"].labels_requested == [s["id"]]  # and Whatnot was asked once


def test_a_label_job_run_again_sends_nothing_a_second_time(app, shop):
    """A worker restarted mid-list gets the same job again (requeue_stale), and any later
    failure retries the whole payload."""
    a, b = ready(app, shop), ready(app, shop, "kat.c")
    with app.app_context():
        ids = list(db.session.scalars(select(WhatnotShipment.id).order_by(WhatnotShipment.id)))
        payload = {
            "org_id": shop["org"],
            "shipment_ids": ids,
            "user_id": None,
            "show_id": shop["show"],
        }
        shipping.labels_job(payload)
        shipping.labels_job(payload)
        shipping.labels_job({**payload, "shipment_ids": list(reversed(ids))})
    assert sorted(shop["fake"].labels_requested) == sorted([a["id"], b["id"]])


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_a_request_that_may_or_may_not_have_reached_whatnot_is_not_sent_again(
    app, client, shop, sign_in
):
    """A timeout after Whatnot took the request looked like a refusal, and the next press
    asked again."""
    from app.services.whatnot.client import Unavailable, WhatnotClient

    s = ready(app, shop)
    h = sign_in(shop["org"], role="manager")
    real = WhatnotClient.request

    def lossy(self, method, path, **kw):
        out = real(self, method, path, **kw)
        if method == "POST":
            raise Unavailable("ReadTimeout calling Whatnot")  # Whatnot did it; we never heard
        return out

    WhatnotClient.request = lossy
    try:
        first = labels(client, shop, h).get_json()
        second = labels(client, shop, h).get_json()
    finally:
        WhatnotClient.request = real
    assert (first["requested"], second["requested"]) == (1, 0)
    assert shop["fake"].labels_requested == [s["id"]]
    pack = second["buyers"][0]["packages"][0]
    assert (pack["labelProblem"], pack["askable"]) == ("unknown", False)
    assert second["labelsToMake"] == 0
    # An hour on and still no label: it may be asked for again.
    with app.app_context():
        row = db.session.scalar(select(WhatnotShipment))
        row.label_requested_at = datetime.now(UTC) - timedelta(minutes=61)
        db.session.commit()
    assert view(app, shop)["labelsToMake"] == 1


def test_a_pickup_is_never_offered_or_sent_for_a_label(app, client, shop, sign_in):
    s = ready(app, shop)
    shop["fake"].set_shipment(s["id"], fulfillment_method="pickup")
    read_shipments(app, shop)
    v = view(app, shop)
    assert (v["buyers"][0]["state"], v["labelsToMake"]) == ("pickup", 0)
    h = sign_in(shop["org"], role="manager")
    assert labels(client, shop, h).get_json()["requested"] == 0
    with app.app_context():  # not even when its id is put in a job by hand
        row_id = db.session.scalar(select(WhatnotShipment.id))
        shipping.labels_job({"org_id": shop["org"], "shipment_ids": [row_id]})
    assert shop["fake"].labels_requested == []


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_no_label_until_the_shows_final_orders_and_parcels_were_read(app, client, shop, sign_in):
    """The show has ended, but a last win may not be here yet: it could be sitting in a
    second open package. Nobody is "ready" on old data."""
    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    with app.app_context():  # as if both reads had happened while the show was still live
        ws = db.session.scalar(select(WhatnotShow))
        ws.orders_synced_at = ws.ended_at - timedelta(minutes=1)
        db.session.commit()
    v = view(app, shop)
    assert (v["buyers"][0]["state"], v["labelsToMake"], v["show"]["complete"]) == (
        "reading",
        0,
        False,
    )
    h = sign_in(shop["org"], role="manager")
    r = labels(client, shop, h)
    assert r.status_code == 409 and "have not been read from Whatnot yet" in r.get_json()["error"]
    assert shop["fake"].labels_requested == []
    # "Read again from Whatnot" reads the orders, then the parcels: now it is ready.
    r = client.post(f"/api/shipping/shows/{shop['show']}/refresh", headers=h).get_json()
    assert (r["buyers"][0]["state"], r["labelsToMake"], r["show"]["complete"]) == ("ready", 1, True)


@pytest.mark.usefixtures("inline_jobs")  # needs the job to have run by the next line
def test_the_count_on_the_button_is_exactly_what_is_sent(app, client, shop, sign_in):
    ready(app, shop, "one")
    ready(app, shop, "two")
    parcel(
        shop,
        "held",
        [order(shop, "held", "Red bundle")],
        required_actions=["package_review_required"],
    )
    read(app, shop)
    read_shipments(app, shop)
    h = sign_in(shop["org"], role="manager")
    assert view(app, shop)["labelsToMake"] == 2
    r = labels(client, shop, h, buyers=["one"]).get_json()
    assert (r["requested"], r["labelsToMake"]) == (1, 1)  # one in flight, one still to make
    r = labels(client, shop, h).get_json()
    assert (r["requested"], r["labelsToMake"]) == (1, 0)
    assert len(shop["fake"].labels_requested) == 2


def test_a_status_the_app_does_not_know_is_never_called_label_ready(app, shop):
    s = ready(app, shop)
    shop["fake"].set_shipment(s["id"], status="label_pending")
    read_shipments(app, shop)
    v = view(app, shop)
    assert v["buyers"][0]["state"] == "other" and v["totals"].get("labelled") is None
    with app.app_context():
        assert db.session.scalar(select(WhatnotShipment.status)) == "Other"


def test_one_order_that_cannot_ship_does_not_hold_back_the_others(app, shop):
    """It used to raise, so nobody was marked shipped, and in auto mode the carrier's news
    for the whole show stayed stuck behind it."""
    shop["say"](app, [win("aaa", "Red bundle"), win("bbb", "Red bundle")])
    run(app, shop["show"])
    a, b = order(shop, "aaa", "Red bundle"), order(shop, "bbb", "Red bundle")
    parcel(shop, "aaa", [a], status="dropped_off", tracking="94A")
    parcel(shop, "bbb", [b], status="dropped_off", tracking="94B")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    with app.app_context():
        from app.models import OrderStatus
        from app.services import orders as orders_service

        mine = db.session.scalar(select(Order).where(Order.buyer_username == "aaa"))
        orders_service.advance(mine, OrderStatus.picking, actor_label="p")
        db.session.commit()
    # A second win for aaa that nobody has named yet lands on the order being picked.
    shop["say"](app, [win("aaa", LOT, "7.00")])
    run(app, shop["show"])
    assert view(app, shop)["toMarkShipped"] == 1  # only bbb: aaa has an item to settle
    with app.app_context():
        assert shipping.mark_shipped(db.session.get(Show, shop["show"])) == 1
        db.session.commit()
        got = {o.buyer_username: o.status.value for o in db.session.scalars(select(Order))}
    assert got == {"aaa": "picking", "bbb": "shipped"}


def test_a_buyer_with_an_order_in_each_recording_has_both_marked_shipped(app, shop):
    from app.models import ChatMessage

    shop["say"](app, [win("lani808", "Red bundle")])
    run(app, shop["show"])
    with app.app_context():
        first = db.session.get(Show, shop["show"])
        second = Show(
            organization_id=shop["org"],
            show_key="second-laptop",
            started_at=first.started_at + timedelta(minutes=20),
            show_date=first.show_date,
        )
        db.session.add(second)
        db.session.flush()
        line = "lani808 won the auction for Red bundle #2 for $26.00"
        db.session.add(
            ChatMessage(
                show_id=second.id, client_id="e" * 16, seq=1, username="", body=line,
                raw_text=line, source="player", captured_at=shop["now"] - timedelta(hours=1),
            )
        )  # fmt: skip
        db.session.commit()
        second_id = second.id
    run(app, second_id)
    a, b = order(shop, "lani808", "Red bundle"), order(shop, "lani808", "Red bundle #2", 2600)
    parcel(shop, "lani808", [a, b], status="in_transit", tracking="94")
    read(app, shop)
    apply(app, shop)
    read_shipments(app, shop)
    assert view(app, shop)["toMarkShipped"] == 2
    with app.app_context():
        assert shipping.mark_shipped(db.session.get(Show, shop["show"])) == 2
        db.session.commit()
        assert {o.status.value for o in db.session.scalars(select(Order))} == {"shipped"}


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/a.pdf",  # 127.0.0.1 as one number
        "https://0x7f.1/a.pdf",
        "https://127.1/a.pdf",
        "https://0/a.pdf",
        "https://localhost./a.pdf",
        "https://foo.localhost/a.pdf",
        "https://metadata.internal/a.pdf",
        "https://labels.example./a.pdf",
        "https://user:pw@labels.example/a.pdf",
        "https://labels.example:8443/a.pdf",
        "https://[::1]/a.pdf",
        "https://intranet/a.pdf",
    ],
)
def test_no_spelling_of_a_local_address_is_fetched(url):
    assert shipping._safe_url(url) is None


def test_a_name_that_resolves_to_a_private_address_is_not_fetched(monkeypatch):
    def resolve(addresses):
        return lambda host, port, **kw: [(2, 1, 6, "", (a, port)) for a in addresses]

    monkeypatch.setattr(shipping.socket, "getaddrinfo", resolve(["93.184.216.34"]))
    assert shipping._public("labels.example") is True
    monkeypatch.setattr(shipping.socket, "getaddrinfo", resolve(["93.184.216.34", "10.0.0.8"]))
    assert shipping._public("rebind.example") is False
    monkeypatch.setattr(shipping.socket, "getaddrinfo", resolve(["169.254.169.254"]))
    assert shipping._public("169.254.169.254.nip.io") is False


def test_tracking_text_from_whatnot_is_cleaned_like_any_text_from_outside(app, shop):
    s = ready(app, shop)
    shop["fake"].set_shipment(s["id"], status="label_generated", tracking_code="94\u202e00\x07AB")
    read_shipments(app, shop)
    with app.app_context():
        row = db.session.scalar(select(WhatnotShipment))
        assert row.tracking == "USPS Ground Advantage 9400AB" and row.tracking_code == "9400AB"


def test_a_hold_whatnot_adds_after_the_button_was_pressed_stops_the_request(app, shop):
    """The job checks each parcel again, under its lock, on what it holds at that moment."""
    s = ready(app, shop)
    with app.app_context():
        row = db.session.scalar(select(WhatnotShipment))
        row_id = row.id
        row.required_actions = ["order_pending_cancellation"]  # read after the press
        db.session.commit()
        shipping.labels_job({"org_id": shop["org"], "shipment_ids": [row_id]})
        assert db.session.scalar(select(WhatnotShipment.label_requested_at)) is None
    assert shop["fake"].labels_requested == [] and s["id"]


def test_a_parcel_still_filling_in_another_show_that_is_live_gets_no_label(
    app, client, shop, sign_in
):
    """Back-to-back shows share a buyer's bundle. The first show has ended, but a label now
    would close the bundle while the buyer is still winning in the second."""
    live = shop["fake"].add_show(
        title="Saturday", status="live", start=shop["now"] - timedelta(minutes=4)
    )
    later = shop["fake"].add_order(
        show_id=live["id"],
        buyer="lani808",
        items=[{"title": "Red bundle", "price_cents": 2500}],
        created=shop["now"] - timedelta(minutes=2),
    )
    parcel(shop, "lani808", [order(shop, "lani808", LOT, 700), later])
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert (v["buyers"][0]["state"], v["labelsToMake"]) == ("shared", 0)
    assert v["buyers"][0]["packages"][0]["liveElsewhere"] is True
    h = sign_in(shop["org"], role="manager")
    assert labels(client, shop, h).get_json()["requested"] == 0
    assert shop["fake"].labels_requested == []
    # The second show ends: the bundle is complete, and the parcel is ready.
    shop["fake"].shows[live["id"]].update(status="ended", ended_at=datetime.now(UTC).isoformat())
    read(app, shop)
    read_shipments(app, shop)
    v = view(app, shop)
    assert (v["buyers"][0]["state"], v["labelsToMake"]) == ("ready", 1)


def test_only_a_read_begun_after_the_show_ended_counts_as_its_final_one(app, shop):
    from app.services.whatnot import sync

    parcel(shop, "lani808", [order(shop, "lani808", "Red bundle")])
    read(app, shop)
    read_shipments(app, shop)
    assert view(app, shop)["show"]["complete"] is True
    with app.app_context():
        ws = db.session.scalar(select(WhatnotShow))
        # The show ends while a read is under way: that read began before the end.
        began = datetime.now(UTC)
        ws.ended_at = began + timedelta(seconds=30)
        sync.sync_show_orders(shop["org"], client_for(shop["org"]), ws)
        db.session.commit()
        assert ws.orders_synced_at < ws.ended_at
    assert view(app, shop)["show"]["complete"] is False

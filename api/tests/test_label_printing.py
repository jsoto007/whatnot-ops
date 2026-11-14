"""Print all the labels of a show: one PDF of the labels not printed yet, in the order of the
order slips; each is marked printed, and nothing of a label is kept. Buyers are made up."""

import io
import json

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.db import db
from app.models import AuditLog, Organization, Show, WhatnotShipment
from app.services import activity
from app.services.whatnot import fake, shipping
from tests import test_whatnot_orders as wo
from tests.test_whatnot_orders import order, read
from tests.test_whatnot_shipping import parcel, read_shipments, view

shop = wo.shop  # the same connected shop and matched show as the orders tests


@pytest.fixture
def labels(app):
    """Label downloads answer with a real one-page PDF, and note the link each came from."""
    fetched: list[str] = []
    broken: set[str] = set()

    def fetch(url: str) -> bytes:
        fetched.append(url)
        if any(b in url for b in broken):
            return b"<html>sign in</html>"
        return fake.label_pdf(url)

    app.config["WHATNOT_FETCH"] = fetch
    yield {"fetched": fetched, "broken": broken}
    app.config.pop("WHATNOT_FETCH", None)


def labelled(app, shop, buyers: dict[str, int]) -> dict[str, dict]:
    """A package with its label made for each buyer: `buyers` is name → how many they won."""
    out = {}
    for name, n in buyers.items():
        orders = [order(shop, name, "Red bundle") for _ in range(n)]
        out[name] = parcel(shop, name, orders, status="label_generated", tracking="94")
    read(app, shop)
    read_shipments(app, shop)
    return out


def press(client, shop, h, **body):
    return client.post(f"/api/shipping/shows/{shop['show']}/labels/print", json=body, headers=h)


def pages(response) -> int:
    return len(PdfReader(io.BytesIO(response.data)).pages)


def counts(response) -> tuple[int, int, int]:
    h = response.headers
    return int(h["X-Labels-Printed"]), int(h["X-Labels-Failed"]), int(h["X-Labels-Left"])


def printed(app, shop) -> dict[str, bool]:
    return {
        b["buyer"]: all(p["printedAt"] for p in b["packages"]) for b in view(app, shop)["buyers"]
    }


def test_the_shows_labels_print_as_one_pdf_in_the_order_of_the_slips(
    app, client, shop, sign_in, labels
):
    made = labelled(app, shop, {"buyer_c": 1, "buyer_a": 1, "buyer_b": 2})
    with app.app_context():
        before = {r.id: r.updated_at for r in db.session.scalars(select(WhatnotShipment))}
    v = view(app, shop)
    assert (v["labelsToPrint"], v["labelsPrinted"]) == (3, 0)

    h = sign_in(shop["org"], role="fulfillment")
    r = press(client, shop, h)
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert r.headers["Cache-Control"] == "no-store" and "inline" in r.headers["Content-Disposition"]
    assert counts(r) == (3, 0, 0) and pages(r) == 3
    # Most items first, then A to Z: the order the slips are printed in. Label and slip, as
    # the single print gives them.
    assert labels["fetched"] == [
        f"https://labels.example/{made[b]['id']}-bundle.pdf"
        for b in ("buyer_b", "buyer_a", "buyer_c")
    ]

    assert printed(app, shop) == {"buyer_b": True, "buyer_a": True, "buyer_c": True}
    v = view(app, shop)
    assert (v["labelsToPrint"], v["labelsPrinted"]) == (0, 3)
    with app.app_context():
        rows = db.session.scalars(select(WhatnotShipment)).all()
        # Printing is not news from Whatnot: when its data last changed stays as it was.
        assert {r.id: r.updated_at for r in rows} == before
        # Nothing of a label is kept: no link, no file.
        assert "labels.example" not in json.dumps([str(x) for r in rows for x in vars(r).values()])
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "labels.printed"))
        assert entry.details == {"printed": 3, "failed": 0, "left": 0}
        assert (
            activity.describe(entry.action, entry.details)
            == "Opened 3 shipping labels of a show to print"
        )

    # Pressed again: nothing is waiting, and nothing is fetched.
    labels["fetched"].clear()
    r = press(client, shop, h)
    assert r.status_code == 409
    assert r.get_json()["error"] == "No label is waiting to be printed for this show."
    assert labels["fetched"] == []


def test_whatnots_packing_slips_can_be_left_out(app, client, shop, sign_in, labels):
    made = labelled(app, shop, {"buyer_a": 1})
    h = sign_in(shop["org"], role="fulfillment")
    r = press(client, shop, h, slips=False)
    assert r.status_code == 200 and counts(r) == (1, 0, 0)
    assert labels["fetched"] == [f"https://labels.example/{made['buyer_a']['id']}.pdf"]
    assert press(client, shop, h, slips="no").status_code == 400


def test_a_label_whatnot_does_not_send_stays_to_print_and_the_others_go(
    app, client, shop, sign_in, labels
):
    made = labelled(app, shop, {"buyer_a": 1, "buyer_b": 1, "buyer_c": 1})
    labels["broken"].add(made["buyer_b"]["id"])
    h = sign_in(shop["org"], role="fulfillment")
    r = press(client, shop, h)
    assert r.status_code == 200 and counts(r) == (2, 1, 1) and pages(r) == 2
    assert printed(app, shop) == {"buyer_a": True, "buyer_b": False, "buyer_c": True}
    with app.app_context():
        entry = db.session.scalar(select(AuditLog).where(AuditLog.action == "labels.printed"))
        assert (
            activity.describe(entry.action, entry.details)
            == "Opened 2 shipping labels of a show to print; Whatnot did not send 1 label"
        )

    # Still broken and the only one left: said in words, and it stays to print.
    r = press(client, shop, h)
    assert r.status_code == 409 and "not a label" in r.get_json()["error"]
    assert printed(app, shop)["buyer_b"] is False

    labels["broken"].clear()
    r = press(client, shop, h)
    assert r.status_code == 200 and counts(r) == (1, 0, 0) and pages(r) == 1
    assert printed(app, shop)["buyer_b"] is True


def test_a_big_show_prints_in_batches(app, client, shop, sign_in, labels):
    buyers = {f"buyer_{n:02d}": 1 for n in range(45)}
    labelled(app, shop, buyers)
    h = sign_in(shop["org"], role="fulfillment")
    seen = []
    for expected in [(20, 0, 25), (20, 0, 5), (5, 0, 0)]:
        r = press(client, shop, h)
        assert r.status_code == 200 and counts(r) == expected and pages(r) == expected[0]
        seen += labels["fetched"]
        labels["fetched"].clear()
    # Every label once, none twice.
    assert len(seen) == 45 == len(set(seen))
    assert press(client, shop, h).status_code == 409


def test_one_print_stops_before_it_grows_too_large(app, shop, labels, monkeypatch):
    labelled(app, shop, {"buyer_a": 1, "buyer_b": 1, "buyer_c": 1})
    one = len(fake.label_pdf("https://labels.example/x.pdf"))
    monkeypatch.setattr(shipping, "MAX_PRINT_BYTES", one * 2 + 100)
    with app.app_context():
        out = shipping.print_labels(db.session.get(Show, shop["show"]))
        db.session.commit()
    # Two fit; the third is the next print, and was not marked.
    assert (out["printed"], out["failed"], out["left"]) == (2, 0, 1)
    assert sorted(printed(app, shop).values()) == [False, True, True]


def test_a_label_printed_on_its_own_is_skipped_by_print_all(app, client, shop, sign_in, labels):
    made = labelled(app, shop, {"buyer_a": 1, "buyer_b": 1})
    h = sign_in(shop["org"], role="fulfillment")
    with app.app_context():
        row_id = db.session.scalar(
            select(WhatnotShipment.id).where(WhatnotShipment.whatnot_id == made["buyer_a"]["id"])
        )
    assert client.get(f"/api/shipping/shipments/{row_id}/label.pdf").status_code == 200
    assert printed(app, shop) == {"buyer_a": True, "buyer_b": False}
    labels["fetched"].clear()
    r = press(client, shop, h)
    assert counts(r) == (1, 0, 0)
    assert labels["fetched"] == [f"https://labels.example/{made['buyer_b']['id']}-bundle.pdf"]
    # One specific label can still be printed again, as often as needed.
    assert client.get(f"/api/shipping/shipments/{row_id}/label.pdf").status_code == 200


def test_only_packages_with_a_label_are_printed(app, client, shop, sign_in, labels):
    labelled(app, shop, {"buyer_a": 1})
    parcel(shop, "buyer_b", [order(shop, "buyer_b", "Red bundle")])  # no label yet
    read(app, shop)
    read_shipments(app, shop)
    h = sign_in(shop["org"], role="fulfillment")
    r = press(client, shop, h)
    assert counts(r) == (1, 0, 0)
    assert printed(app, shop) == {"buyer_a": True, "buyer_b": False}
    assert view(app, shop)["labelsToPrint"] == 0


def test_who_may_print_a_shows_labels(app, client, shop, sign_in, labels):
    labelled(app, shop, {"buyer_a": 1})
    url = f"/api/shipping/shows/{shop['show']}/labels/print"
    # Not signed in; then signed in but without the token every write carries.
    assert client.post(url, json={}).status_code in (401, 403)
    h = sign_in(shop["org"], role="viewer", email="v@x.test")
    assert client.post(url, json={}, headers=h).status_code == 403
    client.post("/api/auth/logout", headers=h)
    h = sign_in(shop["org"], role="fulfillment", email="f@x.test")
    assert client.post(url, json={}).status_code == 403
    assert (
        client.post("/api/shipping/shows/999999/labels/print", json={}, headers=h).status_code
        == 404
    )
    assert labels["fetched"] == []
    client.post("/api/auth/logout", headers=h)

    # Another shop's person cannot print this show's labels.
    with app.app_context():
        other = Organization(name="Other", slug="other")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    h = sign_in(other_id, role="owner", email="other@x.test")
    assert client.post(url, json={}, headers=h).status_code == 404
    assert labels["fetched"] == [] and printed(app, shop) == {"buyer_a": False}


def _pdf_with_pages(n: int) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=288, height=432)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


@pytest.mark.parametrize(
    "bad",
    [
        b"%PDF-1.4\nnot really a pdf",  # says it is a PDF, and is not one
        _pdf_with_pages(0),  # a real PDF with nothing in it
        _pdf_with_pages(shipping.MAX_LABEL_PAGES + 1),  # far too long to be a label
    ],
    ids=["unreadable", "no pages", "too many pages"],
)
def test_a_file_that_is_not_a_label_is_left_out_and_never_marked(
    app, client, shop, sign_in, labels, bad
):
    made = labelled(app, shop, {"buyer_a": 1, "buyer_b": 1})
    good = app.config["WHATNOT_FETCH"]
    app.config["WHATNOT_FETCH"] = lambda url: bad if made["buyer_a"]["id"] in url else good(url)
    h = sign_in(shop["org"], role="fulfillment")
    r = press(client, shop, h)
    assert r.status_code == 200 and counts(r) == (1, 1, 1) and pages(r) == 1
    assert printed(app, shop) == {"buyer_a": False, "buyer_b": True}
    # Alone, it is said in words; it is never a crash, and never an empty page sent to print.
    r = press(client, shop, h)
    assert r.status_code == 409 and "not a label" in r.get_json()["error"]
    assert printed(app, shop)["buyer_a"] is False


def test_labels_that_keep_failing_do_not_hold_up_the_ones_behind_them(
    app, client, shop, sign_in, labels, monkeypatch
):
    made = labelled(app, shop, {f"buyer_{c}": 1 for c in "abcdef"})
    for name in ("buyer_a", "buyer_b", "buyer_c"):  # first in line, and broken every time
        labels["broken"].add(made[name]["id"])
    monkeypatch.setattr(shipping, "LABELS_PER_PRINT", 2)
    h = sign_in(shop["org"], role="fulfillment")
    r = press(client, shop, h)
    assert counts(r) == (2, 3, 4)  # d and e printed; a, b, c and f are still to print
    r = press(client, shop, h)
    assert counts(r) == (1, 3, 3)  # f printed
    assert printed(app, shop) == {
        "buyer_a": False,
        "buyer_b": False,
        "buyer_c": False,
        "buyer_d": True,
        "buyer_e": True,
        "buyer_f": True,
    }


def test_one_press_stops_starting_labels_when_it_has_taken_long_enough(
    app, shop, labels, monkeypatch
):
    import time
    from types import SimpleNamespace

    labelled(app, shop, {"buyer_a": 1, "buyer_b": 1, "buyer_c": 1})
    # The clock as print_labels reads it: the start, the first label, then a minute later.
    ticks = iter([0.0, 0.0, 60.0])
    monkeypatch.setattr(
        shipping,
        "time",
        SimpleNamespace(monotonic=lambda: next(ticks, 60.0), time=time.time, sleep=time.sleep),
    )
    with app.app_context():
        out = shipping.print_labels(db.session.get(Show, shop["show"]))
        db.session.commit()
    assert (out["printed"], out["failed"], out["left"]) == (1, 0, 2)


def test_two_people_pressing_together_do_not_print_the_same_labels(
    app, client, shop, sign_in, labels
):
    from sqlalchemy import func

    labelled(app, shop, {"buyer_a": 1})
    h = sign_in(shop["org"], role="fulfillment")
    with app.app_context():
        ws_id = db.session.get(Show, shop["show"]).whatnot_show_id
        engine = db.engine
    # Someone else's print of this show is under way: it holds the show's printing lock.
    with engine.connect() as other:
        other.execute(select(func.pg_advisory_lock(shipping.PRINT_LOCK, ws_id)))
        try:
            r = press(client, shop, h)
            assert r.status_code == 409
            assert "Someone else is printing" in r.get_json()["error"]
            assert labels["fetched"] == [] and printed(app, shop) == {"buyer_a": False}
        finally:
            other.execute(select(func.pg_advisory_unlock(shipping.PRINT_LOCK, ws_id)))
            other.commit()
    # Theirs has finished: this press goes through.
    assert counts(press(client, shop, h)) == (1, 0, 0)

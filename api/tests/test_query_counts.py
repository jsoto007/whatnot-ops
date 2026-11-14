"""Statement counts that must not grow with the rows: each page below is measured with a few
rows and again with many, and may cost at most a couple of statements more. (A lazy load per
row put 1,000+ statements behind a busy shop's pages.) Also: the rewritten `match_lots` and
the AI candidate pre-filter give the answers they always did."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app.db import db
from app.models import Sale, Sku, WhatnotLot
from app.services import catalog, order_slips, sale_chat, supervisor
from tests.test_pipeline import run

SLACK = 2  # a page may cost this many statements more with 40 rows than with 5


@pytest.fixture
def count_statements(app):
    """`with count_statements() as n: ...` then `n.count` is the statements sent meanwhile."""

    with app.app_context():
        engine = db.engine

    @contextmanager
    def counting():
        box = SimpleNamespace(count=0)

        def bump(*_a, **_k):
            box.count += 1

        event.listen(engine, "before_cursor_execute", bump)
        try:
            yield box
        finally:
            event.remove(engine, "before_cursor_execute", bump)

    return counting


def grow(app, world, start, stop):
    """Wins for buyers start..stop-1: one lot each, half on a known product, half unknown (so
    they wait in Review), all through the real parser."""
    lines = []
    for i in range(start, stop):
        title = "Red bundle" if i % 2 else f"Mystery lot {i} #{i}"
        lines.append(("", f"buyer{i} won the auction for {title} for $25", "player"))
        lines.append((f"buyer{i}", "mine please"))
    world["say"](app, lines)
    run(app, world["show"])
    with app.app_context():
        for i in range(start, stop):  # SKUs with aliases, for the inventory page
            catalog.create_product(
                world["org"],
                f"Thing {i}",
                skus=[{"code": f"T{i}", "priceCents": 100}],
            )
        for sku in db.session.scalars(select(Sku).where(~Sku.aliases.any())):
            catalog.add_alias(world["org"], sku, f"alias of {sku.code}", quiet=True)
        db.session.commit()


def measure(client, count_statements, url):
    assert client.get(url).status_code == 200  # warm: sessions, lazy imports
    with count_statements() as n:
        r = client.get(url)
    assert r.status_code == 200
    return n.count


@pytest.mark.parametrize(
    "url",
    [
        "/api/inventory",
        "/api/dashboard/shows/{show}",
        "/api/shows/{show}/sales",
        "/api/review",
        "/api/dashboard",
    ],
)
def test_pages_do_not_cost_a_statement_per_row(app, world, client, sign_in, count_statements, url):
    sign_in(world["org"])
    url = url.format(show=world["show"])
    grow(app, world, 0, 5)
    few = measure(client, count_statements, url)
    grow(app, world, 5, 40)
    many = measure(client, count_statements, url)
    assert many <= few + SLACK, f"{url}: {few} statements at 5 rows, {many} at 40"


def test_overview_costs_the_same_for_many_shows(app, world, client, sign_in, count_statements):
    from datetime import timedelta

    from app.models import Show

    sign_in(world["org"])
    few = measure(client, count_statements, "/api/dashboard")
    with app.app_context():
        base = db.session.get(Show, world["show"])
        for i in range(15):
            db.session.add(
                Show(
                    organization_id=base.organization_id,
                    show_key=f"extra-{i}",
                    started_at=base.started_at - timedelta(days=i + 1),
                    show_date=base.show_date - timedelta(days=i + 1),
                )
            )
        db.session.commit()
    many = measure(client, count_statements, "/api/dashboard")
    assert many <= few + SLACK, f"{few} statements for 1 show, {many} for 16"


# ---------- order slips: the indexed match is the old scan ----------


def _old_match_lots(lots, sales):
    free = {s.id: s for s in sales}
    out = {}
    by_order = {s.external_order_id: s for s in sales if s.external_order_id}
    for lot in lots:
        s = by_order.get(lot.whatnot_order_id)
        if s is not None and s.id in free:
            out[lot.id] = free.pop(s.id)
    numbered = [(s, sale_chat.lot_of(s)) for s in sales]

    def take(lot, same_price):
        for s, n in numbered:
            if s.id not in free or n is None or n != lot.lot_number:
                continue
            if s.buyer_username.lower() != lot.recipient.lower():
                continue
            if same_price and (s.price_cents is None or s.price_cents != lot.price_cents):
                continue
            out[lot.id] = free.pop(s.id)
            return

    for same_price in (True, False):
        for lot in lots:
            if lot.id not in out and lot.lot_number is not None:
                take(lot, same_price)
    return out


def test_match_lots_gives_what_the_scan_gave_ties_included():
    def sale(i, buyer, lot, price, order=None):
        return Sale(
            id=i,
            buyer_username=buyer,
            item_text=f"Item #{lot}" if lot else "plain",
            price_cents=price,
            external_order_id=order,
        )

    def lot(i, who, n, price, order):
        return WhatnotLot(
            id=i, recipient=who, lot_number=n, price_cents=price, whatnot_order_id=order
        )

    sales = [
        sale(1, "Ann", 5, 1000),
        sale(2, "ann", 5, 1000),  # a tie with 1: the earlier one goes first
        sale(3, "Ann", 5, 900),
        sale(4, "Bob", 7, None),
        sale(5, "Bob", 7, 500),
        sale(6, "Cy", 9, 100, order="O-9"),
        sale(7, "Cy", 9, 100),
        sale(8, "Di", None, 100),
    ]
    lots = [
        lot(10, "ANN", 5, 900, "O-1"),  # same price beats order of sales: sale 3
        lot(11, "Ann", 5, 1000, "O-2"),  # sale 1
        lot(12, "Ann", 5, 1000, "O-3"),  # sale 2
        lot(13, "Ann", 5, 1000, "O-4"),  # nothing left at that price: any free of Ann #5
        lot(14, "Bob", 7, 500, "O-5"),  # sale 5 by price, though 4 is earlier
        lot(15, "Bob", 7, 123, "O-6"),  # falls to the loose pass: sale 4
        lot(16, "Cy", 9, 100, "O-9"),  # by order number: sale 6
        lot(17, "Cy", 9, 100, "O-10"),  # sale 7
        lot(18, "Di", None, 100, "O-11"),  # no lot number: never matched
        lot(19, "Eve", 3, 1, "O-12"),  # no such buyer
    ]
    new = {k: v.id for k, v in order_slips.match_lots(lots, sales).items()}
    old = {k: v.id for k, v in _old_match_lots(lots, sales).items()}
    assert new == old
    assert new[10] == 3 and new[11] == 1 and new[12] == 2 and new[14] == 5 and new[15] == 4


# ---------- the AI's candidate list ----------


def _catalog(app, world, n):
    with app.app_context():
        for i in range(n):
            catalog.create_product(
                world["org"], f"Gadget {i}", skus=[{"code": f"G{i:03d}", "priceCents": 100}]
            )
        db.session.commit()


def test_candidates_send_the_whole_catalog_up_to_sixty(app, world):
    _catalog(app, world, 59)  # RED is the 60th
    with app.app_context():
        assert len(supervisor._candidates(world["org"], "anything at all")) == 60


def test_candidates_keep_the_product_the_title_names_in_a_big_catalog(app, world):
    _catalog(app, world, 100)
    with app.app_context():
        sku = db.session.scalar(select(Sku).where(Sku.code == "G087"))
        catalog.add_alias(world["org"], sku, "sparkly widget", quiet=True)
        db.session.commit()
        got = supervisor._candidates(world["org"], "the Sparkly Widget!! #12 x")
        assert len(got) == supervisor.CANDIDATES_TOP
        # Gadget 12 shares "12" and 87 shares "sparkly widget": both lead, in score order.
        assert got[0]["skuId"] == sku.id
        # Nothing shares a word: the first forty, in the catalog's order.
        none = supervisor._candidates(world["org"], "zzz qqq")
        ids = list(db.session.scalars(select(Sku.id).order_by(Sku.id).limit(40)))
        assert [c["skuId"] for c in none] == ids


def test_candidates_rank_by_shared_words_then_catalog_order(app, world):
    _catalog(app, world, 80)
    with app.app_context():
        two = db.session.scalar(select(Sku).where(Sku.code == "G070"))
        catalog.add_alias(world["org"], two, "blue gadget pack", quiet=True)
        db.session.commit()
        got = supervisor._candidates(world["org"], "blue gadget pack")
        assert got[0]["skuId"] == two.id  # three shared words beat one ("gadget")
        rest = [c["skuId"] for c in got[1:]]
        assert rest == sorted(rest)  # the rest share "gadget" alone: catalog order

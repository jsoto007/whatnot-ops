"""`flask whatnot-probe`: a look at the live shop through the Seller API, reads only.

Whatnot has no sandbox, so the first calls ever made are made carefully: GET requests only
(anything else is refused before it is sent), a few per second, a few dozen in all. The
report says what SHAPE the data has (which fields are filled, what an id looks like, which
statuses occur). It never prints a username, an amount, an address, a title, a full id, a
link or the token, so it can be pasted into a ticket or a chat.

It answers what the integration could only assume from the docs (the W0 questions in
claude-reports/Whatnot-Seller-API/one-stop-shop-plan-2026-11-10.md).
"""

import re
import time
from collections import Counter
from collections.abc import Callable

from .client import Forbidden, Response, WhatnotClient, WhatnotError, when

MAX_CALLS = 60
PER_SECOND = 4
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class ProbeBudget(Exception):
    pass


def read_only(transport, *, sleep: Callable[[float], None] = time.sleep, seen: dict | None = None):
    """Wrap a transport so it can only read, slowly, and not for long."""
    calls = {"n": 0}

    def send(method: str, url: str, **kw) -> Response:
        if method != "GET":
            raise RuntimeError(f"the probe is read-only; refused to send {method}")
        if calls["n"] >= MAX_CALLS:
            raise ProbeBudget()
        calls["n"] += 1
        sleep(1 / PER_SECOND)
        r = transport(method, url, **kw)
        if seen is not None:
            seen["rate_headers"] = any(k.startswith("ratelimit-") for k in r.headers)
            seen["calls"] = calls["n"]
        return r

    return send


def id_shape(value) -> str:
    """What an id looks like, never the id: "decimal, 10 digits" / "uuid" / "missing"."""
    if value is None:
        return "missing"
    s = str(value)
    if _UUID.match(s):
        return "uuid"
    if s.isdigit():
        return f"decimal, {len(s)} digits"
    return f"text, {len(s)} chars"


def title_shape(title) -> str:
    """How a listing title is built, without the title."""
    if not isinstance(title, str) or not title.strip():
        return "empty"
    parts = ["text"]
    if re.search(r"#\s*\d+\s*$", title):
        parts.append("ends in a lot number (#N)")
    elif re.search(r"\d+\s*$", title):
        parts.append("ends in a number")
    if re.search(r"choose|pick|your choice", title, re.I):
        parts.append('says "choose/pick"')
    return ", ".join(parts)


def filled(rows: list[dict], field: str) -> str:
    """ "5/5" rows where the field holds something."""
    n = sum(1 for r in rows if r.get(field) not in (None, "", [], {}))
    return f"{n}/{len(rows)}"


def _why(e: WhatnotError) -> str:
    if isinstance(e, Forbidden) and e.required_scopes:
        return f"refused ({e.status} {e.problem}); the token needs: {', '.join(e.required_scopes)}"
    return f"failed ({e.status} {e.problem or type(e).__name__})"


def run(client: WhatnotClient, seen: dict | None = None) -> list[str]:
    """Make the calls and return the report's lines."""
    out: list[str] = []
    say = out.append

    def both(*parts: str) -> None:
        say("- " + "; ".join(parts))

    def section(title: str, fn) -> None:
        say(f"\n## {title}")
        try:
            fn()
        except ProbeBudget:
            say("- stopped: the probe's call budget is used up")
        except WhatnotError as e:
            say(f"- {_why(e)}")

    state: dict = {}

    def account():
        me = client.me()
        say(f"- reachable: yes; seller id is {id_shape(me.get('id'))}")
        say(f"- can go live: {me.get('can_go_live')}; currency {me.get('seller_currency')}")

    def shows():
        for status in ("live", "ended", "scheduled"):
            rows = _take(client.shows(status, limit=5), 5)
            say(f"- {status}: {len(rows)} (first page of 5)")
            if rows and status in ("live", "ended"):
                state.setdefault("show", rows[0])
        show = state.get("show")
        if show:
            in_url = str(show.get("id")) in str(show.get("url") or "")
            both(
                f"show id is {id_shape(show.get('id'))}",
                f"its dashboard link contains the id: {in_url}",
            )
            say(
                "- scheduled_start_at filled: "
                f"{show.get('scheduled_start_at') is not None}; ended_at filled: "
                f"{show.get('ended_at') is not None}"
            )

    def orders():
        rows = _take(client.orders(limit=10), 10)
        state["orders"] = rows
        if not rows:
            say("- no orders")
            return
        say(
            f"- order id is {id_shape(rows[0].get('id'))} "
            "(the Shipments page shows a 10-digit Order #)"
        )
        say(f"- statuses: {dict(Counter(str(r.get('status')) for r in rows))}")
        say(f"- sale types: {dict(Counter(str(r.get('transaction_type')) for r in rows))}")
        say(f"- buyer filled: {filled(rows, 'buyer')}; show_id filled: {filled(rows, 'show_id')}")
        amounts = rows[0].get("amounts") or {}
        say(f"- amounts carry: {sorted(k for k, v in amounts.items() if v is not None)}")
        both(
            f"updated_at filled: {filled(rows, 'updated_at')}",
            f"cancelled_at filled: {filled(rows, 'cancelled_at')}",
        )

    def order_items():
        items = []
        for order in state.get("orders", [])[:3]:
            items += client.order_items(order["id"])
        state["order_items"] = items
        if not items:
            say("- no items read")
            return
        bought = [i.get("purchased_item") or {} for i in items]
        say(f"- items read: {len(items)} from 3 orders; item id is {id_shape(items[0].get('id'))}")
        say(f"- title shapes: {dict(Counter(title_shape(b.get('title')) for b in bought))}")
        say(f"- purchased sku filled: {filled(bought, 'sku')}")
        both(
            f"product_id filled: {filled(items, 'product_id')}",
            f"variant_id filled: {filled(items, 'variant_id')}",
        )
        both(
            f"shipment_id filled: {filled(items, 'shipment_id')}",
            f"unit_price filled: {filled(items, 'unit_price')}",
        )
        say(f"- quantities: {dict(Counter(i.get('quantity') for i in items))}")

    def shipments():
        rows = _take(client.shipments(limit=10), 10)
        state["shipments"] = rows
        if not rows:
            say("- no shipments")
            return
        say(f"- shipment id is {id_shape(rows[0].get('id'))}")
        say(f"- statuses: {dict(Counter(str(r.get('status')) for r in rows))}")
        both(
            f"recipient filled: {filled(rows, 'recipient')}",
            f"documents filled: {filled(rows, 'documents')}",
        )
        docs = [r.get("documents") or {} for r in rows]
        both(
            f"label link filled: {filled(docs, 'label_url')}",
            f"label+slip link filled: {filled(docs, 'bundled_url')}",
        )
        both(
            f"tracking filled: {filled(rows, 'tracking')}",
            f"dropoff_due filled: {filled(rows, 'dropoff_due')}",
        )
        actions = Counter(a for r in rows for a in (r.get("required_actions") or []))
        say(f"- required actions seen: {dict(actions) or 'none'}")
        both(
            f"weight filled: {filled(rows, 'weight')}",
            f"replacement id filled: {filled(rows, 'replacement_shipment_id')}",
        )

    def shipment_items():
        per_shipment = []
        items = []
        for shipment in state.get("shipments", [])[:3]:
            got = client.shipment_items(shipment["id"])
            per_shipment.append(len(got))
            items += got
        if not items:
            say("- no items read")
            return
        say(f"- items per shipment (3 read): {per_shipment}")
        both(
            f"order_id filled: {filled(items, 'order_id')}",
            f"order_item_id filled: {filled(items, 'order_item_id')}",
        )
        say(f"- its order_id is {id_shape(items[0].get('order_id'))}")
        orders_per_shipment = Counter(i.get("shipment_id") for i in items if i.get("order_id"))
        say(
            "- so one shipment can hold several orders: "
            f"{max(orders_per_shipment.values(), default=0) > 1}"
        )

    def catalog():
        rows = _take(client.products(limit=5), 5)
        say(f"- products: {len(rows)} (first page of 5)")
        if not rows:
            return
        both(
            f"product id is {id_shape(rows[0].get('id'))}",
            f"external_id filled: {filled(rows, 'external_id')}",
        )
        variants = client.variants(rows[0]["id"])
        both(
            f"variants of the first: {len(variants)}",
            f"variant id is {id_shape(variants[0].get('id')) if variants else 'missing'}",
        )
        both(
            f"variant sku filled: {filled(variants, 'sku')}",
            f"price filled: {filled(variants, 'price')}",
        )
        merch = client.merchandising(rows[0]["id"])
        both(
            f"sale format of the first: {merch.get('transaction_type')}",
            f"reserved for live: {merch.get('reserved_for_live')}",
        )

    def profiles():
        say(f"- dimension profiles: {len(client.dimension_profiles())}")

    def analytics():
        show = state.get("show")
        if not show:
            say("- no live or ended show to ask about")
            return
        a = client.show_analytics(show["id"])
        say(f"- calculation status: {a.get('calculation_status')}")
        metrics = [
            k for k in a if k not in ("show_id", "title", "start_time", "calculation_status")
        ]
        say(f"- metrics filled: {sum(1 for k in metrics if a.get(k) is not None)}/{len(metrics)}")

    def reports():
        rows = _take(client.reports(limit=5), 5)
        both(
            f"report runs so far: {len(rows)}",
            f"types: {dict(Counter(str(r.get('report_type')) for r in rows))}",
        )

    def paging():
        show = state.get("show")
        start = when((show or {}).get("scheduled_start_at"))
        if start is None:
            say("- no show start to count back to")
            return
        n, cap = 0, 300
        for order in client.orders(limit=50):
            made = when(order.get("created_at"))
            if made is None or made < start or n >= cap:
                break
            n += 1
        pages = max(1, -(-n // 50))
        more = " or more (stopped there)" if n >= cap else ""
        say(f"- orders since the latest show's start: {n}{more}, about {pages} page(s) of 50")

    section("Account", account)
    section("Shows", shows)
    section("Orders", orders)
    section("Order items", order_items)
    section("Shipments", shipments)
    section("Shipment items", shipment_items)
    section("Catalog", catalog)
    section("Shipping profiles", profiles)
    section("Show analytics", analytics)
    section("Reports", reports)
    section("How far back one show reaches", paging)
    if seen:
        say(
            f"\n- calls made: {seen.get('calls', 0)}; "
            f"rate-limit headers present: {seen.get('rate_headers')}"
        )
    return out


def _take(rows, n: int) -> list[dict]:
    out = []
    for row in rows:
        out.append(row)
        if len(out) >= n:
            break
    return out

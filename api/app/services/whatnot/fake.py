"""An in-memory Whatnot for tests: the Seller API's reads and writes as its OpenAPI
description (v2.0.0, read 2026-11-10) gives them, behind the same transport the real
client uses. So a test exercises the real client: paging, retries, error mapping, money.

    fake = FakeWhatnot()
    app.config["WHATNOT_TRANSPORT"] = fake.transport
    order = fake.add_order(show_id=show["id"], buyer="lani808", items=[{"title": "Lot #1"}])

Shapes are the documented ones, not ones observed from the live shop (no call had been made
when this was written). Say so when quoting what a test here proves.
"""

import base64
import itertools
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .client import Response

TOKEN = "wn_pat_tk_test"
# The signing secret Whatnot's own Webhooks page uses for its example delivery
# (developers.whatnot.com): the bytes 1 to 32. Public, not a key of ours. It is built
# here instead of written out so a secret scanner does not take a documentation example
# for a leaked key.
EXAMPLE_WEBHOOK_SECRET = "wn_wh_" + base64.b64encode(bytes(range(1, 33))).decode()


def _iso(t: datetime | None) -> str | None:
    return t.isoformat().replace("+00:00", "Z") if t else None


def money(cents: int | None, currency: str = "USD") -> dict | None:
    if cents is None:
        return None
    sign, cents = ("-" if cents < 0 else ""), abs(cents)
    return {"amount": f"{sign}{cents // 100}.{cents % 100:02d}", "currency": currency}


class FakeWhatnot:
    def __init__(self, *, seller_id: str = "1234567", username: str = "host_shop"):
        self.seller = {
            "id": seller_id,
            "username": username,
            "display_name": None,
            "image": None,
            "seller_currency": "USD",
            "country_code": "US",
            "can_go_live": True,
        }
        self.token = TOKEN
        self.scopes: set[str] | None = None  # None: every scope
        self.shows: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.order_items: dict[str, dict] = {}
        self.shipments: dict[str, dict] = {}
        self.shipment_items: dict[str, dict] = {}
        self.products: dict[str, dict] = {}
        self.variants: dict[str, dict] = {}
        self.merch: dict[str, dict] = {}
        self.analytics: dict[str, dict] = {}
        self.reports: dict[str, dict] = {}
        self.report_files: dict[str, str] = {}
        self.idempotency: dict[str, dict] = {}
        self.labels_requested: list[str] = []
        self.cancelled: list[tuple[str, str]] = []
        self.calls: list[tuple[str, str]] = []  # (method, path) of every attempt
        self.page_size_cap = 50
        self._ids = itertools.count(1000000001)
        self._clock = datetime(2026, 11, 8, 2, 0, tzinfo=UTC)
        self._failures: deque[Response] = deque()

    # ---- building the shop ----

    def _next_id(self) -> str:
        return str(next(self._ids))

    def tick(self, seconds: int = 1) -> datetime:
        self._clock += timedelta(seconds=seconds)
        return self._clock

    def fail_next(self, status: int, problem: str = "", *, retry_after: str | None = None, **extra):
        """The next attempt gets this error instead of an answer (queue several for more)."""
        body = {
            "type": problem,
            "title": problem,
            "status": status,
            "detail": "from Whatnot",
            **extra,
        }
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        self._failures.append(Response(status, body, headers))

    def add_show(
        self,
        *,
        title: str = "Friday night show",
        status: str = "ended",
        start: datetime | None = None,
        ended: datetime | None = None,
        show_id: str | None = None,
    ) -> dict:
        now = self.tick()
        show = {
            "id": show_id or str(uuid.uuid4()),
            "title": title,
            "url": "https://www.whatnot.com/dashboard/live/x",
            "description": None,
            "status": status,
            "visibility": "public",
            "scheduled_start_at": _iso(start or now),
            "ended_at": _iso(ended) if ended else (_iso(now) if status == "ended" else None),
            "thumbnail": None,
            "created_at": _iso(now),
            "updated_at": _iso(now),
        }
        self.shows[show["id"]] = show
        return show

    def add_order(
        self,
        *,
        show_id: str | None,
        buyer: str,
        items: list[dict],
        status: str = "pending_fulfillment",
        transaction_type: str = "auction",
        created: datetime | None = None,
        shipping_cents: int = 525,
        buyer_id: str | None = None,
    ) -> dict:
        """One order with its items. Each item: title, price_cents (default 100), quantity,
        and optionally sku / product_id / variant_id."""
        now = created or self.tick()
        order_id = self._next_id()
        subtotal = 0
        for spec in items:
            price = spec.get("price_cents", 100)
            qty = spec.get("quantity", 1)
            subtotal += price * qty
            item_id = self._next_id()
            self.order_items[item_id] = {
                "id": item_id,
                "order_id": order_id,
                "product_id": spec.get("product_id"),
                "variant_id": spec.get("variant_id"),
                "product_external_id": None,
                "variant_external_id": None,
                "purchased_item": {
                    "title": spec.get("title", "Lot"),
                    "description": None,
                    "sku": spec.get("sku"),
                },
                "quantity": qty,
                "unit_price": money(price),
                "price_includes_tax": False,
                "shipment_id": None,
            }
        order = {
            "id": order_id,
            "url": f"https://www.whatnot.com/dashboard/orders/{order_id}",
            "status": status,
            "show_id": show_id,
            "transaction_type": transaction_type,
            "buyer": {"id": buyer_id or f"u-{buyer}", "username": buyer, "display_name": None},
            "amounts": {
                "subtotal": money(subtotal),
                "shipping": money(shipping_cents),
                "tax": money(0),
                "applied_discount": None,
                "total": money(subtotal + shipping_cents),
            },
            "created_at": _iso(now),
            "updated_at": _iso(now),
            "cancelled_at": None,
        }
        self.orders[order_id] = order
        return order

    def items_of(self, order_id: str) -> list[dict]:
        return [i for i in self.order_items.values() if i["order_id"] == order_id]

    def set_order_status(self, order_id: str, status: str) -> dict:
        order = self.orders[order_id]
        now = self.tick()
        order["status"], order["updated_at"] = status, _iso(now)
        if status == "cancelled":
            order["cancelled_at"] = _iso(now)
        return order

    def add_shipment(
        self,
        *,
        recipient: str,
        order_ids: list[str],
        status: str = "created",
        tracking: str | None = None,
        created: datetime | None = None,
        required_actions: list[str] | None = None,
        dropoff_due: datetime | None = None,
    ) -> dict:
        """A parcel holding every item of these orders (Whatnot bundles a buyer's wins)."""
        now = created or self.tick()
        shipment_id = self._next_id()
        shipment = {
            "id": shipment_id,
            "url": f"https://www.whatnot.com/dashboard/shipments/{shipment_id}",
            "status": status,
            "fulfillment_method": "shipping",
            "recipient": {"id": f"u-{recipient}", "username": recipient, "display_name": None},
            "weight": None,
            "dimensions": None,
            "signature_required": False,
            "hazardous_material": "not_hazardous",
            "tracking": {
                "carrier": "USPS",
                "service": "Ground Advantage",
                "tracking_code": tracking,
                "last_mile_tracking_code": None,
            }
            if tracking
            else None,
            "documents": {
                "label_url": f"https://labels.example/{shipment_id}.pdf",
                "bundled_url": f"https://labels.example/{shipment_id}-bundle.pdf",
                "commercial_invoice_url": None,
            }
            if status != "created"
            else None,
            "insurance": None,
            "seller_paid_shipping_cost": None,
            "required_actions": required_actions or [],
            "dropoff_due": _iso(dropoff_due),
            "replacement_shipment_id": None,
            "created_at": _iso(now),
            "updated_at": _iso(now),
        }
        self.shipments[shipment_id] = shipment
        for order_id in order_ids:
            self.put_on_shipment(order_id, shipment_id)
        return shipment

    def put_on_shipment(self, order_id: str, shipment_id: str) -> None:
        """Move an order's items onto a shipment (off whichever held them)."""
        now = self.tick()
        for item in self.items_of(order_id):
            for sid, si in list(self.shipment_items.items()):
                if si["order_item_id"] == item["id"]:
                    del self.shipment_items[sid]
            sid = self._next_id()
            self.shipment_items[sid] = {
                "id": sid,
                "shipment_id": shipment_id,
                "order_id": order_id,
                "order_item_id": item["id"],
                "dimension_profile": None,
                "hazardous_material": "not_hazardous",
                "country_of_origin": None,
                "created_at": _iso(now),
                "updated_at": _iso(now),
            }
            item["shipment_id"] = shipment_id
        self.shipments[shipment_id]["updated_at"] = _iso(now)

    def set_shipment(self, shipment_id: str, **changes) -> dict:
        shipment = self.shipments[shipment_id]
        tracking = changes.pop("tracking_code", None)
        shipment.update(changes)
        if tracking:
            shipment["tracking"] = {
                "carrier": "USPS",
                "service": "Ground Advantage",
                "tracking_code": tracking,
                "last_mile_tracking_code": None,
            }
        if shipment["status"] != "created" and shipment["documents"] is None:
            shipment["documents"] = {
                "label_url": f"https://labels.example/{shipment_id}.pdf",
                "bundled_url": f"https://labels.example/{shipment_id}-bundle.pdf",
                "commercial_invoice_url": None,
            }
        shipment["updated_at"] = _iso(self.tick())
        return shipment

    def add_product(
        self, *, title: str, variants: list[dict] | None = None, external_id: str | None = None
    ) -> dict:
        """A product with its variants. Each variant: sku, price_cents, quantity, options."""
        now = self.tick()
        product_id = self._next_id()
        product = {
            "id": product_id,
            "title": title,
            "description": None,
            "external_id": external_id,
            "url": f"https://www.whatnot.com/dashboard/inventory/{product_id}",
            "category": {"id": "1", "name": "Beauty"},
            "attributes": [],
            "options": [],
            "media": [],
            "dimension_profile": None,
            "created_at": _iso(now),
            "updated_at": _iso(now),
        }
        self.products[product_id] = product
        self.merch[product_id] = {
            "transaction_type": None,
            "reserved_for_live": False,
            "active_in_shows": False,
            "show_ids": [],
        }
        for spec in variants or [{}]:
            variant_id = str(uuid.uuid4())
            self.variants[variant_id] = {
                "id": variant_id,
                "product_id": product_id,
                "sku": spec.get("sku"),
                "external_id": spec.get("external_id"),
                "selected_options": spec.get("options", []),
                "price": money(spec.get("price_cents")),
                "country_of_origin": None,
                "inventory": {"available_quantity": spec.get("quantity", 0)},
                "media": [],
            }
        return product

    def variants_of(self, product_id: str) -> list[dict]:
        return [v for v in self.variants.values() if v["product_id"] == product_id]

    # ---- the transport ----

    def transport(
        self, method: str, url: str, *, headers: dict, params: dict | None, json
    ) -> Response:
        path = urlsplit(url).path.split("/seller-api/rest/v2", 1)[-1] or "/"
        self.calls.append((method, path))
        if headers.get("Authorization") != f"Bearer {self.token}":
            return self._problem(401, "unauthenticated")
        if self._failures:
            return self._failures.popleft()
        params = params or {}
        parts = [p for p in path.split("/") if p]
        try:
            handler = getattr(self, f"_{method.lower()}_{parts[0] if parts else 'root'}", None)
            if handler is None:
                return self._problem(404, "endpoint-not-found")
            return handler(parts[1:], params, json)
        except KeyError:
            return self._problem(404, f"{parts[0].rstrip('s')}-not-found")

    @staticmethod
    def _problem(status: int, problem: str, **extra) -> Response:
        return Response(
            status,
            {
                "type": problem,
                "title": problem,
                "status": status,
                "detail": "from Whatnot",
                **extra,
            },
        )

    def _needs(self, *scopes: str) -> Response | None:
        if self.scopes is not None and not any(s in self.scopes for s in scopes):
            return self._problem(403, "missing-scope", required_scopes=list(scopes))
        return None

    def _page(self, rows: list[dict], params: dict, *, key="created_at") -> Response:
        reverse = str(params.get("reverse", "true")).lower() != "false"
        rows = sorted(rows, key=lambda r: (r.get(key) or "", r["id"]), reverse=reverse)
        limit = min(int(params.get("limit", 10)), self.page_size_cap)
        start = int(params.get("cursor") or 0)
        chunk = rows[start : start + limit]
        more = start + limit < len(rows)
        return Response(200, {"data": chunk, "next_cursor": str(start + limit) if more else None})

    def _get_root(self, rest, params, body) -> Response:
        return Response(200, {"status": "ok"})

    def _get_users(self, rest, params, body) -> Response:
        return Response(200, dict(self.seller))

    def _get_shows(self, rest, params, body) -> Response:
        if denied := self._needs("read:shows"):
            return denied
        if rest:
            return Response(200, dict(self.shows[rest[0]]))
        status = params.get("status", "scheduled")
        rows = [s for s in self.shows.values() if s["status"] == status]
        return self._page(rows, params, key="scheduled_start_at")

    def _get_analytics(self, rest, params, body) -> Response:
        if denied := self._needs("read:reports"):
            return denied
        show = self.shows[rest[1]]
        return Response(
            200,
            self.analytics.get(show["id"])
            or {
                "show_id": show["id"],
                "title": show["title"],
                "start_time": show["scheduled_start_at"],
                "calculation_status": "NOT_ENDED" if show["status"] != "ended" else "PENDING",
            },
        )

    def _order_view(self, order: dict) -> dict:
        out = dict(order)
        if self.scopes is not None and "read:customers" not in self.scopes:
            out["buyer"] = None
        return out

    def _get_orders(self, rest, params, body) -> Response:
        if denied := self._needs("read:orders", "write:orders"):
            return denied
        if not rest:
            return self._page([self._order_view(o) for o in self.orders.values()], params)
        order = self.orders[rest[0]]
        if len(rest) == 1:
            return Response(200, self._order_view(order))
        items = self.items_of(order["id"])
        if len(rest) == 2:
            return self._page(items, params, key="id")
        return Response(200, dict(self.order_items[rest[2]]))

    def _post_orders(self, rest, params, body) -> Response:
        if denied := self._needs("write:orders"):
            return denied
        order = self.orders[rest[0]]
        if order["status"] in ("completed", "cancelled"):
            return self._problem(409, "order-not-cancellable")
        self.cancelled.append((order["id"], (body or {}).get("reason", "")))
        return Response(200, self._order_view(self.set_order_status(order["id"], "cancelled")))

    def _shipment_view(self, shipment: dict) -> dict:
        out = dict(shipment)
        if self.scopes is not None and "read:customers" not in self.scopes:
            out["recipient"] = out["documents"] = None
        return out

    def _get_shipments(self, rest, params, body) -> Response:
        if denied := self._needs("read:shipments", "write:shipments"):
            return denied
        if not rest:
            return self._page([self._shipment_view(s) for s in self.shipments.values()], params)
        shipment = self.shipments[rest[0]]
        if len(rest) == 1:
            return Response(200, self._shipment_view(shipment))
        items = [i for i in self.shipment_items.values() if i["shipment_id"] == shipment["id"]]
        if len(rest) == 2:
            return self._page(items, params, key="id")
        return Response(200, dict(self.shipment_items[rest[2]]))

    def _post_shipments(self, rest, params, body) -> Response:
        if denied := self._needs("write:shipments"):
            return denied
        if rest == ["add_tracking_code"]:
            return self._problem(409, "tracking-code-not-allowed")
        shipment = self.shipments[rest[0]]
        if rest[1:] != ["generate_label"]:
            return self._problem(404, "endpoint-not-found")
        if shipment["status"] != "created" or shipment["required_actions"]:
            return self._problem(409, "label-generation-rejected")
        self.labels_requested.append(shipment["id"])
        return Response(200, {"shipment_id": shipment["id"]})

    def finish_label(self, shipment_id: str, tracking: str = "9400111899223344556677") -> dict:
        """What Whatnot's worker does some time after generate_label was accepted."""
        return self.set_shipment(shipment_id, status="label_generated", tracking_code=tracking)

    def _get_products(self, rest, params, body) -> Response:
        if denied := self._needs("read:inventory", "write:inventory"):
            return denied
        if not rest:
            return self._page(list(self.products.values()), params)
        product = self.products[rest[0]]
        if len(rest) == 1:
            return Response(200, dict(product))
        variants = self.variants_of(product["id"])
        if len(rest) == 2:
            return self._page(variants, params, key="sku")
        return Response(200, dict(self.variants[rest[2]]))

    def _get_merchandising(self, rest, params, body) -> Response:
        if denied := self._needs("read:merchandising", "write:merchandising"):
            return denied
        merch = self.merch[rest[1]]
        if len(rest) == 2:
            return Response(200, {k: v for k, v in merch.items() if k != "show_ids"})
        rows = [
            self.shows[s]
            for s in merch["show_ids"]
            if self.shows[s]["status"] in ("scheduled", "live")
        ]
        return self._page(rows, params, key="scheduled_start_at")

    def _post_merchandising(self, rest, params, body) -> Response:
        if denied := self._needs("write:merchandising"):
            return denied
        product_id = rest[1]
        merch = self.merch[product_id]
        body = body or {}
        variants = self.variants_of(product_id)
        if any(v["price"] is None for v in variants):
            return self._problem(422, "merchandising-rejected", errors=[{"field": "price"}])
        if body.get("transaction_type"):
            merch["transaction_type"] = body["transaction_type"]
            if body["transaction_type"] == "auction":
                merch["reserved_for_live"] = True
        if body.get("reserve_for_live") is not None:
            merch["reserved_for_live"] = bool(body["reserve_for_live"])
        shows = body.get("shows")
        if shows:
            ids = [s for s in shows["show_ids"] if s in self.shows]
            if len(ids) != len(shows["show_ids"]):
                return self._problem(404, "show-not-found")
            if shows["strategy"] == "ADD":
                merch["show_ids"] = list(dict.fromkeys(merch["show_ids"] + ids))
            elif shows["strategy"] == "REMOVE":
                merch["show_ids"] = [s for s in merch["show_ids"] if s not in ids]
            else:
                merch["show_ids"] = ids
        merch["active_in_shows"] = bool(merch["show_ids"])
        return Response(
            200,
            {
                "product_id": product_id,
                "transaction_type": merch["transaction_type"],
                "reserved_for_live": merch["reserved_for_live"],
            },
        )

    def _post_products(self, rest, params, body) -> Response:
        if denied := self._needs("write:inventory"):
            return denied
        body = body or {}
        if not body.get("title"):
            return self._problem(422, "invalid-product")
        product = self.add_product(title=body["title"], external_id=body.get("external_id"))
        product["description"] = body.get("description")
        product["dimension_profile"] = body.get("dimension_profile")
        product["media"] = body.get("media", [])
        return Response(201, dict(product))

    def _patch_products(self, rest, params, body) -> Response:
        if denied := self._needs("write:inventory"):
            return denied
        product = self.products[rest[0]]
        body = body or {}
        if rest[1:] != ["variants"]:
            product.update({k: v for k, v in body.items() if v is not None})
            product["updated_at"] = _iso(self.tick())
            return Response(200, dict(product))
        patches = body.get("variants") or []
        if len(patches) > 10:
            return self._problem(422, "invalid-parameter", param="variants")
        updated = []
        for patch in patches:
            variant = self.variants[patch["id"]]
            for field in ("sku", "external_id", "price", "country_of_origin"):
                if field in patch:
                    variant[field] = patch[field]
            updated.append(dict(variant))
        return Response(207, {"variants": updated, "errors": None})

    def _post_inventory(self, rest, params, body) -> Response:
        if denied := self._needs("write:inventory"):
            return denied
        items = (body or {}).get("items") or []
        if len(items) > 10:
            return self._problem(422, "invalid-parameter", param="items")
        results = []
        for item in items:
            key = item["idempotency_key"]
            if key in self.idempotency:
                results.append({**self.idempotency[key], "status": "already_applied"})
                continue
            variant = self.variants.get(item["variant_id"])
            if variant is None:
                results.append(
                    {
                        "variant_id": item["variant_id"],
                        "idempotency_key": key,
                        "status": "variant_not_found",
                        "previous_quantity": None,
                        "new_quantity": None,
                        "warnings": [],
                    }
                )
                continue
            before = variant["inventory"]["available_quantity"]
            wanted = item["quantity"] if rest == ["set"] else before + item["delta"]
            after = max(0, wanted)
            variant["inventory"]["available_quantity"] = after
            result = {
                "variant_id": variant["id"],
                "idempotency_key": key,
                "status": "applied",
                "previous_quantity": before,
                "new_quantity": after,
                "warnings": ["result_clamped_to_zero"] if wanted < 0 else [],
            }
            self.idempotency[key] = result
            results.append(result)
        return Response(207, {"results": results})

    def _get_shipping(self, rest, params, body) -> Response:
        return self._page([], params)

    def _post_reports(self, rest, params, body) -> Response:
        if denied := self._needs("write:reports"):
            return denied
        parameters = (body or {}).get("report_parameters") or {}
        report_type = parameters.get("report_type")
        if report_type not in (
            "seller_financial_report",
            "analytics_overview_report",
            "analytics_livestreams_report",
        ):
            return self._problem(422, "invalid-parameter", param="report_type")
        now = self.tick()
        report_id = str(uuid.uuid4())
        self.reports[report_id] = {
            "id": report_id,
            "report_type": report_type,
            "status": "pending",
            "report_parameters": parameters,
            "report_url": None,
            "created_at": _iso(now),
            "updated_at": _iso(now),
        }
        return Response(201, dict(self.reports[report_id]))

    def finish_report(self, report_id: str, csv_text: str) -> dict:
        """What Whatnot's worker does: the run completes and gets a download link."""
        report = self.reports[report_id]
        url = f"https://reports.example/{report_id}.csv"
        self.report_files[url] = csv_text
        report.update(status="completed", report_url=url, updated_at=_iso(self.tick()))
        return report

    def _get_reports(self, rest, params, body) -> Response:
        if denied := self._needs("read:reports", "write:reports"):
            return denied
        if rest:
            return Response(200, dict(self.reports[rest[0]]))
        rows = list(self.reports.values())
        if params.get("status"):
            rows = [r for r in rows if r["status"] == params["status"]]
        return self._page(rows, params)

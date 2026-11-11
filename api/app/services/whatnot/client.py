"""The one place that talks to Whatnot's Seller API (REST v2).

Everything else calls `client_for(org_id)` and the typed helpers below. The token comes
from the environment (`WHATNOT_API_TOKEN`) and is never logged, stored or put in an error.

What the client takes care of:
  * pacing: Whatnot allows 10 requests a second per seller; `throttle` (shared across the
    web processes and the worker, see throttle.py) is called before every attempt;
  * retries: a 429 waits for `Retry-After`, a 5xx or a dropped connection backs off with
    jitter. A write is only retried when it is safe to send twice (a 429 was never
    processed; inventory writes carry their own idempotency keys);
  * errors: RFC 9457 problem types become typed exceptions. `words` is what a person may
    read; Whatnot's own text stays in the log;
  * money: `cents()` turns Whatnot's decimal strings ("42.00") into integer cents.

There is no sandbox: every call is the live shop. Tests never get here (see `client_for`).
"""

import logging
import random
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal, InvalidOperation

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.whatnot.com/seller-api/rest/v2"
PAGE_LIMIT = 50  # the most any list endpoint returns per page
TIMEOUT = (5, 20)  # connect, read
MAX_ATTEMPTS = 4
MAX_RETRY_AFTER = 30  # never sleep longer than this on one 429


class WhatnotError(Exception):
    """A call that did not work. `words` is safe to show; `problem` is Whatnot's own slug."""

    words = "Whatnot did not answer as expected. Try again in a minute."
    #: What our own route answers with: 409 when the request cannot be done as things
    #: stand, 502 when the trouble is on the way to Whatnot (its answer, or our access).
    http_status = 409

    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        problem: str = "",
        request_id: str = "",
        detail: str = "",
    ):
        super().__init__(message or self.words)
        self.status = status
        self.problem = problem
        self.request_id = request_id
        self.detail = detail  # Whatnot's wording: for the log, never for the page


class NotConfigured(WhatnotError):
    words = "Whatnot is not set up on this server yet. Ask your developer to add the access token."


class NotConnected(WhatnotError):
    words = "This shop is not connected to Whatnot yet. The owner can connect it in Settings."


class Unauthorized(WhatnotError):
    words = "Whatnot no longer accepts the access token. The account owner needs to make a new one."
    http_status = 502


class Forbidden(WhatnotError):
    words = "The Whatnot access token is not allowed to do that."
    http_status = 502

    def __init__(self, *a, required_scopes: list[str] | None = None, **kw):
        super().__init__(*a, **kw)
        self.required_scopes = required_scopes or []


class NotFound(WhatnotError):
    words = "Whatnot does not have that any more."


class Conflict(WhatnotError):
    words = "Whatnot would not do that in the state things are in right now."


class Invalid(WhatnotError):
    words = "Whatnot did not accept that request."


class Busy(WhatnotError):
    words = "Whatnot is asking us to slow down. Try again in a minute."
    http_status = 502


class Unavailable(WhatnotError):
    words = "Whatnot could not be reached. Try again in a minute."
    http_status = 502


_BY_STATUS = {401: Unauthorized, 403: Forbidden, 404: NotFound, 409: Conflict, 429: Busy}

# A problem type that says something more useful than its status code does.
PROBLEM_WORDS = {
    "seller-verification-required": "Whatnot needs the seller to finish verification before it "
    "allows this. Open Seller Hub to see what it asks for.",
    "seller-not-approved": "Whatnot has not turned this on for the shop's account yet.",
    "variants-disabled": "Whatnot has not turned this on for the shop's account yet.",
    "inventory-writes-disabled": "Whatnot has not turned stock changes on for the shop's "
    "account yet.",
    "label-generation-rejected": "Whatnot cannot make that label right now. Open the shipment "
    "in Seller Hub to see what it needs.",
    "order-not-cancellable": "Whatnot says that order can no longer be cancelled.",
    "missing-scope": "The Whatnot access token is missing a permission this needs. The account "
    "owner can add it to the token in Seller Hub.",
}


def cents(money) -> int | None:
    """Whatnot's Money ({"amount": "42.00", "currency": "USD"}) as integer cents.
    Never through a float; None for a missing or unreadable amount."""
    if not isinstance(money, dict):
        return None
    amount = money.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, str | int):
        return None
    try:
        value = Decimal(str(amount).strip())
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return int((value * 100).quantize(Decimal(1)))


def when(value) -> datetime | None:
    """An ISO 8601 timestamp from the API as a tz-aware datetime (UTC when it names no
    zone); None when missing or unreadable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import UTC

        parsed = parsed.replace(tzinfo=UTC)
    return parsed


class Response:
    """What a transport returns: the three things the client reads."""

    def __init__(self, status: int, body=None, headers: dict[str, str] | None = None):
        self.status = status
        self.body = body
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


Transport = Callable[..., Response]


def http_transport(session: requests.Session | None = None) -> Transport:
    session = session or requests.Session()

    def send(method: str, url: str, *, headers: dict, params: dict | None, json) -> Response:
        try:
            r = session.request(
                method, url, headers=headers, params=params, json=json, timeout=TIMEOUT
            )
        except requests.RequestException as e:
            # The exception's text can carry the URL but never the token (it is a header).
            raise Unavailable(f"{type(e).__name__} calling Whatnot") from None
        try:
            body = r.json() if r.content else None
        except ValueError:
            body = None
        return Response(r.status_code, body, dict(r.headers))

    return send


class WhatnotClient:
    def __init__(
        self,
        token: str,
        *,
        transport: Transport | None = None,
        throttle: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        base_url: str = BASE_URL,
        max_attempts: int = MAX_ATTEMPTS,
    ):
        if not token:
            raise NotConfigured()
        self._token = token
        self._send = transport or http_transport()
        self._throttle = throttle or (lambda: None)
        self._sleep = sleep
        self._base = base_url.rstrip("/")
        self._max_attempts = max_attempts
        self.calls = 0  # attempts sent, for the sync's own bookkeeping

    def __repr__(self) -> str:  # never the token
        return f"<WhatnotClient {self._base}>"

    # ---- the request loop ----

    def request(
        self, method: str, path: str, *, params: dict | None = None, json=None, safe: bool = False
    ):
        """Send one call and return its JSON body (None for 204). `safe` marks a write that
        may be sent twice (it carries idempotency keys); reads always may."""
        retry_errors = method == "GET" or safe
        request_id = uuid.uuid4().hex
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "X-Request-ID": request_id,
        }
        if json is not None:
            headers["Content-Type"] = "application/json"
        params = {k: v for k, v in (params or {}).items() if v is not None} or None
        attempt = 0
        while True:
            attempt += 1
            self._throttle()
            self.calls += 1
            try:
                r = self._send(method, self._base + path, headers=headers, params=params, json=json)
            except Unavailable:
                if retry_errors and attempt < self._max_attempts:
                    self._sleep(self._backoff(attempt))
                    continue
                raise
            if r.status < 300:
                return r.body
            if r.status == 429 and attempt < self._max_attempts:
                self._sleep(self._retry_after(r, attempt))
                continue
            if r.status >= 500 and retry_errors and attempt < self._max_attempts:
                self._sleep(self._backoff(attempt))
                continue
            raise self._error(r, method, path, request_id)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(20.0, 0.5 * 2 ** (attempt - 1)) + random.uniform(0, 0.25)

    def _retry_after(self, r: Response, attempt: int) -> float:
        try:
            wait = float(r.headers.get("retry-after", ""))
        except ValueError:
            wait = 1.0
        # Whatnot's own advice: wait what it says, then double on each further 429.
        wait = max(wait, 1.0) * 2 ** (attempt - 1)
        return min(MAX_RETRY_AFTER, wait) + random.uniform(0, 0.25)

    @staticmethod
    def _error(r: Response, method: str, path: str, request_id: str) -> WhatnotError:
        body = r.body if isinstance(r.body, dict) else {}
        problem = body.get("type") if isinstance(body.get("type"), str) else ""
        detail = body.get("detail")
        detail = detail if isinstance(detail, str) else ""  # a 422's detail is a list
        cls = _BY_STATUS.get(r.status) or (Unavailable if r.status >= 500 else Invalid)
        kw = {"status": r.status, "problem": problem, "request_id": request_id, "detail": detail}
        if cls is Forbidden:
            scopes = body.get("required_scopes")
            kw["required_scopes"] = [s for s in scopes if isinstance(s, str)] if scopes else []
        err = cls(f"Whatnot {r.status} {problem or '-'} on {method} {path}", **kw)
        if problem in PROBLEM_WORDS:
            err.words = PROBLEM_WORDS[problem]
        log.warning(
            "whatnot %s %s -> %s %s (request %s)", method, path, r.status, problem, request_id
        )
        return err

    def get(self, path: str, **params):
        return self.request("GET", path, params=params)

    def pages(
        self, path: str, *, reverse: bool | None = None, limit: int = PAGE_LIMIT, **params
    ) -> Iterator[dict]:
        """Every item of a cursor-paged list, in the API's order (newest first unless
        `reverse=False`). Stop reading whenever you have enough: pages are fetched lazily."""
        cursor = None
        while True:
            page = self.request(
                "GET",
                path,
                params={
                    **params,
                    "limit": limit,
                    "cursor": cursor,
                    "reverse": None if reverse is None else str(reverse).lower(),
                },
            )
            page = page if isinstance(page, dict) else {}
            for item in page.get("data") or []:
                if isinstance(item, dict):
                    yield item
            cursor = page.get("next_cursor")
            if not cursor:
                return

    # ---- reads ----

    def me(self) -> dict:
        return self.get("/users/me") or {}

    def shows(self, status: str, **kw) -> Iterator[dict]:
        return self.pages("/shows", status=status, **kw)

    def show(self, show_id: str) -> dict:
        return self.get(f"/shows/{show_id}") or {}

    def show_analytics(self, show_id: str) -> dict:
        return self.get(f"/analytics/shows/{show_id}") or {}

    def orders(self, **kw) -> Iterator[dict]:
        return self.pages("/orders", **kw)

    def order(self, order_id: str) -> dict:
        return self.get(f"/orders/{order_id}") or {}

    def order_items(self, order_id: str) -> list[dict]:
        return list(self.pages(f"/orders/{order_id}/items"))

    def shipments(self, **kw) -> Iterator[dict]:
        return self.pages("/shipments", **kw)

    def shipment(self, shipment_id: str) -> dict:
        return self.get(f"/shipments/{shipment_id}") or {}

    def shipment_items(self, shipment_id: str) -> list[dict]:
        return list(self.pages(f"/shipments/{shipment_id}/items"))

    def products(self, **kw) -> Iterator[dict]:
        return self.pages("/products", **kw)

    def product(self, product_id: str) -> dict:
        return self.get(f"/products/{product_id}") or {}

    def variants(self, product_id: str) -> list[dict]:
        return list(self.pages(f"/products/{product_id}/variants"))

    def merchandising(self, product_id: str) -> dict:
        return self.get(f"/merchandising/products/{product_id}") or {}

    def product_shows(self, product_id: str) -> list[dict]:
        return list(self.pages(f"/merchandising/products/{product_id}/shows"))

    def dimension_profiles(self) -> list[dict]:
        return list(self.pages("/shipping/dimension_profiles"))

    def reports(self, **kw) -> Iterator[dict]:
        return self.pages("/reports", **kw)

    def report(self, report_id: str) -> dict:
        return self.get(f"/reports/{report_id}") or {}


def client_for(org_id: int) -> WhatnotClient:
    """The shop's client, or NotConnected / NotConfigured. Paced by the shared limiter."""
    from . import connection, throttle

    if connection.active(org_id) is None:
        raise NotConnected()
    return make_client(throttle=lambda: throttle.acquire(org_id))


def make_client(*, throttle: Callable[[], None] | None = None) -> WhatnotClient:
    """A client on the server's token. In tests there is no way to the real API: a test
    installs a fake transport (`app.config["WHATNOT_TRANSPORT"]`) or the call fails."""
    from flask import current_app

    token = current_app.config.get("WHATNOT_API_TOKEN") or ""
    transport = current_app.config.get("WHATNOT_TRANSPORT")
    if transport is None and current_app.config.get("TESTING"):
        raise RuntimeError("a test tried to reach Whatnot: install a FakeWhatnot transport")
    if not token:
        raise NotConfigured()
    kw = (
        {"sleep": current_app.config["WHATNOT_SLEEP"]}
        if "WHATNOT_SLEEP" in current_app.config
        else {}
    )
    return WhatnotClient(token, transport=transport, throttle=throttle, **kw)

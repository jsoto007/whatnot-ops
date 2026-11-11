"""Whatnot Seller API integration.

  client.py      the only code that calls Whatnot (pacing, retries, typed errors, money)
  throttle.py    one request budget shared by the web processes and the worker
  connection.py  which Whatnot seller a shop is, and whether calls are working
  webhooks.py    signed deliveries from Whatnot: verify, store once, hand to a job
  fake.py        an in-memory Whatnot for tests (no test may reach the real API)

Whatnot decides what was sold, whether it was paid and where a parcel is. The app decides
which product it was, the gifts, and the picking and packing. See docs/WHATNOT_API.md.
"""

from .client import (
    Busy,
    Conflict,
    Forbidden,
    Invalid,
    NotConfigured,
    NotConnected,
    NotFound,
    Unauthorized,
    Unavailable,
    WhatnotClient,
    WhatnotError,
    cents,
    client_for,
    make_client,
    when,
)

__all__ = [
    "Busy",
    "Conflict",
    "Forbidden",
    "Invalid",
    "NotConfigured",
    "NotConnected",
    "NotFound",
    "Unauthorized",
    "Unavailable",
    "WhatnotClient",
    "WhatnotError",
    "cents",
    "client_for",
    "make_client",
    "when",
]

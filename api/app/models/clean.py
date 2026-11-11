"""Free text people type (names, notes, reasons) loses control characters and invisible
marks on its way into a column, whichever route or service sets it.

A bell character in an order note used to be stored as typed: invisible on the page, it
made openpyxl refuse the Excel export (IllegalCharacterError), so every report that
included that order answered 500 until someone found and edited the note. Captured chat
and uploaded files are already cleaned in `services/text.py`; this covers what staff type.
"""

from sqlalchemy import event

from ..services.text import clean
from .accounts import User
from .buyers import Buyer
from .core import Device, DevicePairing, Organization
from .inventory import InventoryMovement, Product, ProductAlias, Sku
from .orders import Order

SINGLE_LINE = [
    (Organization, "name"),
    (Organization, "host_username"),
    (Device, "name"),
    (DevicePairing, "name"),
    (User, "name"),
    (Product, "name"),
    (Sku, "name"),
    (Sku, "code"),
    (ProductAlias, "alias"),
    (InventoryMovement, "reason"),
    (Order, "tracking"),
    (Order, "packed_by"),
]
MULTI_LINE = [(Product, "notes"), (Order, "notes"), (Buyer, "note")]


def _cleaner(single_line: bool):
    def on_set(_target, value, _old, _initiator):
        return clean(value, single_line=single_line) if isinstance(value, str) else value

    return on_set


for model, column in SINGLE_LINE:
    event.listen(getattr(model, column), "set", _cleaner(True), retval=True)
for model, column in MULTI_LINE:
    event.listen(getattr(model, column), "set", _cleaner(False), retval=True)

"""Excel workbooks with openpyxl. Money is cents in the database and currency cells in the file."""

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import db
from ..models import InventoryMovement, Order, Organization, ParsedEvent, Product, Sale, Show, Sku
from . import activity, dashboard, sale_chat
from .showdate import SHOP_TZ

HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="002244")
MONEY = '"$"#,##0.00'


def _sheet(
    wb: Workbook,
    title: str,
    header: list[str],
    rows: list[list],
    *,
    money_cols: tuple[int, ...] = (),
    first: bool = False,
):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title[:31]
    ws.append(header)
    for c in ws[1]:
        c.font = HEAD
        c.fill = FILL
        c.alignment = Alignment(vertical="center")
    for k, r in enumerate(rows, start=2):
        # Text saved before models/clean.py may hold control characters, which openpyxl
        # refuses: one old order note must not fail the whole report.
        r = [ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in r]
        ws.append(r)
        # Chat text, notes and names are written as text, never as formulas: a buyer or
        # item named "=HYPERLINK(...)" must not become a live formula in the owner's Excel.
        # Index the row just written: `ws[ws.max_row]` rescans the sheet (O(rows²)).
        for j, v in enumerate(r, start=1):
            if isinstance(v, str) and v.startswith("="):
                ws.cell(row=k, column=j).data_type = "s"
    for col in money_cols:
        for row in ws.iter_rows(min_row=2, min_col=col, max_col=col):
            for cell in row:
                if isinstance(cell.value, int | float):
                    cell.value = cell.value / 100
                    cell.number_format = MONEY
    for i, h in enumerate(header, start=1):
        width = (
            max(
                [len(str(h))]
                + [len(str(r[i - 1])) for r in rows if i - 1 < len(r) and r[i - 1] is not None][
                    :200
                ]
            )
            + 2
        )
        ws.column_dimensions[get_column_letter(i)].width = min(60, max(10, width))
    ws.freeze_panes = "A2"
    return ws


CHAT_CHARS = 2000  # per cell; the full chat is on the show's page


def _chat_cell(lines: list[dict]) -> str:
    text = "\n".join(f"#{c['seq']} {c['username'] or 'platform'}: {c['body']}" for c in lines)
    return text if len(text) <= CHAT_CHARS else text[: CHAT_CHARS - 1] + "…"


def _sale_rows(sales: list[Sale]) -> list[list]:
    chat = sale_chat.around(sales)
    return [
        [
            s.show.show_date.isoformat(),
            s.buyer_username,
            s.sku.product.name if s.sku else s.item_text,
            s.sku.code if s.sku else "",
            s.quantity,
            s.price_cents,
            (s.price_cents or 0) * s.quantity,
            activity.lot(s.auction_type),
            activity.word(activity.SALE_STATUS, s.status),
            s.external_order_id or "",
            s.paid_at.isoformat() if s.paid_at else "",
            s.status_reason or "",
            f"{w.astimezone(SHOP_TZ):%Y-%m-%d %H:%M:%S}" if (w := sale_chat.won_at(s)) else "",
            _chat_cell(chat[s.id]),
        ]
        for s in sales
    ]


SALE_HEADER = [
    "Show",
    "Buyer",
    "Item",
    "SKU",
    "Qty",
    "Price",
    "Total",
    "Type",
    "Status",
    "Whatnot order",
    "Paid at",
    "Note",
    "Won at (Central)",
    "Buyer chat",
]


def _sales(
    org_id: int, *, show_id: int | None = None, since: date | None = None, until: date | None = None
) -> list[Sale]:
    q = (
        select(Sale)
        .join(Show, Show.id == Sale.show_id)
        .where(Sale.organization_id == org_id)
        .options(
            selectinload(Sale.sku).selectinload(Sku.product),
            selectinload(Sale.show),
            selectinload(Sale.event).selectinload(ParsedEvent.message),
        )
        .order_by(Show.started_at, Sale.id)
    )
    if show_id:
        q = q.where(Sale.show_id == show_id)
    if since:
        q = q.where(Show.show_date >= since)
    if until:
        q = q.where(Show.show_date <= until)
    return db.session.scalars(q).all()


def build(
    org: Organization,
    *,
    show: Show | None = None,
    since: date | None = None,
    until: date | None = None,
) -> bytes:
    """One workbook: sales, customers, products, inventory, cancellations, revenue, fulfilment."""
    sales = _sales(org.id, show_id=show.id if show else None, since=since, until=until)
    standing = [s for s in sales if s.status in dashboard.STANDING]
    wb = Workbook()

    # 1. Sales by show
    _sheet(wb, "Sales by show", SALE_HEADER, _sale_rows(standing), money_cols=(6, 7), first=True)

    # 2. Customer purchase history
    by_buyer: dict[str, dict] = {}
    for s in standing:
        b = by_buyer.setdefault(
            s.buyer_username.lower(),
            {"buyer": s.buyer_username, "items": 0, "paid": 0, "shows": set(), "last": ""},
        )
        b["items"] += s.quantity
        if s.status in dashboard.PAID:
            b["paid"] += (s.price_cents or 0) * s.quantity
        b["shows"].add(s.show.show_date.isoformat())
        b["last"] = max(b["last"], s.show.show_date.isoformat())
    _sheet(
        wb,
        "Customers",
        ["Buyer", "Items", "Paid", "Shows", "Last show"],
        sorted(
            (
                [b["buyer"], b["items"], b["paid"], len(b["shows"]), b["last"]]
                for b in by_buyer.values()
            ),
            key=lambda r: (-r[1], r[0]),
        ),
        money_cols=(3,),
    )

    # 3. Products sold
    by_sku: dict[str, dict] = {}
    for s in standing:
        key = s.sku.code if s.sku else f"? {s.item_text}"
        p = by_sku.setdefault(
            key,
            {
                "code": s.sku.code if s.sku else "",
                "product": s.sku.product.name if s.sku else s.item_text,
                "qty": 0,
                "paid": 0,
                "buyers": set(),
            },
        )
        p["qty"] += s.quantity
        if s.status in dashboard.PAID:
            p["paid"] += (s.price_cents or 0) * s.quantity
        p["buyers"].add(s.buyer_username.lower())
    _sheet(
        wb,
        "Products sold",
        ["SKU", "Product", "Qty sold", "Paid", "Buyers"],
        sorted(
            (
                [p["code"], p["product"], p["qty"], p["paid"], len(p["buyers"])]
                for p in by_sku.values()
            ),
            key=lambda r: (-r[2], r[0]),
        ),
        money_cols=(4,),
    )

    # 4. Inventory remaining (current levels, org-wide)
    skus = db.session.scalars(
        select(Sku)
        .join(Product)
        .where(Sku.organization_id == org.id)
        .options(selectinload(Sku.product))
        .order_by(Product.name, Sku.code)
    ).all()
    _sheet(
        wb,
        "Inventory remaining",
        [
            "Product",
            "SKU",
            "On hand",
            "Reserved",
            "Available",
            "To buy",
            "Low at",
            "Price",
            "Weight (oz)",
        ],
        [
            [
                s.product.name,
                s.code,
                s.on_hand,
                s.reserved,
                s.available,
                s.short,
                s.low_stock_at,
                s.price_cents,
                None if s.weight_oz is None else float(s.weight_oz),
            ]
            for s in skus
        ],
        money_cols=(8,),
    )

    # 5. Cancellations and failed payments
    lost = [s for s in sales if s.status in dashboard.LOST]
    _sheet(wb, "Cancellations", SALE_HEADER, _sale_rows(lost), money_cols=(6, 7))

    # 6. Revenue by show
    tiles = (
        [dashboard.show_tiles(show)]
        if show
        else dashboard.shows_table(org.id, limit=1000, since=since, until=until)
    )
    _sheet(
        wb,
        "Revenue",
        [
            "Show",
            "Messages",
            "Items sold",
            "Buyers",
            "Paid revenue",
            "Pending revenue",
            "Cancelled / failed",
            "Gifts",
            "No-picks",
            "No winner",
            "Red",
            "Bundles",
            "Ultra",
            "Mega",
            "Quick Hit",
        ],
        [
            [
                t["date"],
                t["messages"],
                t["itemsSold"],
                t["buyers"],
                t["revenuePaidCents"],
                t["revenuePendingCents"],
                t["cancelled"],
                t["gifts"],
                t["noPicks"],
                t["noWinner"],
                t["red"],
                t["bundles"],
                t["ultra"],
                t["mega"],
                t["quickHit"],
            ]
            for t in tiles
        ],
        money_cols=(5, 6),
    )

    # 7. Winners with nothing to pack (same rows as the show page's "Walked away" tab)
    shows_for_walkaways = (
        [show]
        if show
        else db.session.scalars(
            select(Show)
            .where(Show.id.in_({s.show_id for s in sales} or {0}))
            .order_by(Show.started_at)
        ).all()
    )
    _sheet(
        wb,
        "Walked away",
        ["Show", "Buyer", "Outcome", "Item", "Qty", "Line #", "Note"],
        [
            [
                sh.show_date.isoformat(),
                r["buyer"],
                r["label"],
                r["item"],
                r["quantity"],
                r["seq"],
                r["note"],
            ]
            for sh in shows_for_walkaways
            for r in dashboard.winners_without_purchase(sh)
        ],
    )

    # 8. Fulfilment list (verified orders not yet shipped)
    oq = (
        select(Order)
        .where(Order.organization_id == org.id, Order.status.in_(("paid", "picking", "packed")))
        .options(selectinload(Order.show), selectinload(Order.sales).selectinload(Sale.sku))
        .order_by(Order.id)
    )
    if show:
        oq = oq.where(Order.show_id == show.id)
    rows = []
    for o in db.session.scalars(oq).all():
        for s in o.sales:
            if s.status in dashboard.PAID:
                rows.append(
                    [
                        o.show.show_date.isoformat(),
                        o.buyer_username,
                        activity.word(activity.ORDER_STATUS, o.status),
                        s.sku.code if s.sku else "",
                        s.sku.product.name if s.sku else s.item_text,
                        s.quantity,
                        o.tracking or "",
                        o.notes or "",
                    ]
                )
    _sheet(
        wb,
        "Fulfilment",
        ["Show", "Buyer", "Order status", "SKU", "Item", "Qty", "Tracking", "Notes"],
        rows,
    )

    # 8. Stock movements in range (audit trail)
    mq = (
        select(InventoryMovement)
        .where(InventoryMovement.organization_id == org.id)
        .options(selectinload(InventoryMovement.sku))
        .order_by(InventoryMovement.id)
    )
    if since:
        mq = mq.where(InventoryMovement.created_at >= since)
    moves = db.session.scalars(mq.limit(5000)).all()
    names = activity.user_names(org.id, {m.actor_id for m in moves if m.actor_type == "user"})
    _sheet(
        wb,
        "Stock movements",
        ["When", "SKU", "Kind", "Qty", "Source", "By", "Reason", "On hand after", "Reserved after"],
        [
            [
                m.created_at.isoformat(timespec="seconds"),
                m.sku.code,
                activity.word(activity.MOVEMENT_KIND, m.kind),
                m.quantity,
                activity.movement_source(m.source_type, m.source_id),
                activity.movement_actor(m.actor_type, m.actor_id, m.actor_label, names),
                m.reason or "",
                m.on_hand_after,
                m.reserved_after,
            ]
            for m in moves
        ],
    )

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

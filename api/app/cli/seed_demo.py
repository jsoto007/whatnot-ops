"""`flask seed-demo`: a realistic local dataset for looking at the app. Refused in production.

Creates the organization "Demo Card Shop" with one owner and one viewer,
five products with stock, three shows of chat (two reconciled and partly
shipped, one live), a review item and a device. Sign in as
demo-owner@whatnot-ops.local with the password in DEMO_PASSWORD below.
"""

import secrets
from datetime import UTC, datetime, timedelta

import click
from sqlalchemy import select

from ..db import db
from ..models import ChatMessage, Order, OrderStatus, Organization, Role, Show, User
from ..services import accounts, catalog, inventory, orders, pipeline, reconcile
from ..services.devices import create_device
from ..services.inventory import Actor, MovementKind
from ..services.showdate import show_date

DEMO_SLUG = "demo-card-shop"
DEMO_OWNER = "demo-owner@whatnot-ops.local"
DEMO_VIEWER = "demo-viewer@whatnot-ops.local"
DEMO_PASSWORD = "demo-owner-pass-2026"  # local demo only

PRODUCTS = [
    ("Red bundle", "RED", 2500, 18, ["red", "red bundle"]),
    ("Ultra pack", "ULTRA", 4000, 9, ["ultra", "ultra pack"]),
    ("Mega bundle", "MEGA", 6000, 5, ["mega", "mega bundle"]),
    ("Holo slab", "HOLO", 1500, 12, ["holo", "slab"]),
    ("Sticker pack", "STICKER", 500, 40, ["stickers", "sticker pack"]),
]

HOST = "demo_shop_live"
BUYERS = [
    "lani808",
    "pokemom_22",
    "cardguy",
    "kat.c",
    "dave-77",
    "mintcollector",
    "jess_pulls",
    "tony.tcg",
]


def _lines(day: int) -> list[tuple[str, str]]:
    """A show's chat: listings, claims, platform 'won' lines, a gift, a no-pick, a cancel."""
    b = BUYERS
    base = [
        ("", f"{b[0]} joined"),
        (b[0], "hi everyone!"),
        (HOST, "Auction #1: Red bundle"),
        (b[1], "mine"),
        (b[0], "I'll take 2"),
        ("", f"🎉 {b[0]} won the auction for $25.00"),
        (HOST, "Auction #2: Ultra pack"),
        (b[2], "sold"),
        ("", f"{b[2]} won Ultra pack for $40"),
        (HOST, "Up next - Mega bundle"),
        (b[3], "2 please"),
        ("", f"{b[3]} won Mega bundle x2 for $120"),
        (b[4], "do you ship to canada?"),
        (HOST, "Auction #4: Holo slab"),
        ("", "No bids"),
        (HOST, f"gift to {b[1]} - sticker pack"),
        ("", f"{b[1]} won the giveaway!"),
        (HOST, "Auction #5: Red bundle"),
        ("", f"{b[5]} won Red bundle x3"),
        (b[5], "thank you!!"),
        (HOST, "Auction #6: Holo slab"),
        ("", f"{b[6]} won Holo slab for $15"),
        (HOST, f"cancelled for {b[6]}"),
        ("", f"{b[7]} bought mystery box for $10"),
        (HOST, "Auction #7: Sticker pack"),
        ("", f"{b[4]} won Sticker pack x4 for $20"),
        (b[2], "that was fast lol"),
    ]
    if day == 1:
        base += [("", f"{b[1]} won Red bundle for $25"), (HOST, f"payment failed for {b[1]}")]
    if day == 2:
        base += [
            (HOST, "Auction #8: Ultra pack"),
            ("", f"{b[0]} won Ultra pack x2 for $80"),
            (b[0], "my payment went through, thanks"),
        ]
    return base


def register(app):
    @app.cli.command("seed-demo")
    def seed_demo():
        """Local demo data (refused in production)."""
        if app.config.get("APP_ENV") == "production":
            raise click.ClickException("refused in production")
        from ..config import db_host

        click.echo(f"database: {db_host(app.config['SQLALCHEMY_DATABASE_URI'])}")
        if db.session.scalar(select(Organization.id).where(Organization.slug == DEMO_SLUG)):
            raise click.ClickException(
                "demo org already exists; drop and recreate the local database to reseed"
            )

        org = Organization(name="Demo Card Shop", slug=DEMO_SLUG, host_username=HOST)
        db.session.add(org)
        db.session.flush()
        owner = User(
            organization=org,
            email=DEMO_OWNER,
            name="Olive Owner",
            role=Role.owner,
            password_hash=accounts._ph.hash(DEMO_PASSWORD),
            session_token=secrets.token_hex(16),
        )
        viewer = User(
            organization=org,
            email=DEMO_VIEWER,
            name="Vic Viewer",
            role=Role.viewer,
            password_hash=accounts._ph.hash(DEMO_PASSWORD),
            session_token=secrets.token_hex(16),
        )
        db.session.add_all([owner, viewer])
        db.session.flush()
        create_device(org, "Front laptop")
        actor = Actor.user(owner)

        for name, code, price, qty, aliases in PRODUCTS:
            p = catalog.create_product(org.id, name, skus=[{"code": code, "priceCents": price}])
            sku = p.skus[0]
            sku.low_stock_at = 3
            for a in aliases:
                catalog.add_alias(org.id, sku, a, quiet=True)
            inventory.move(
                sku.id,
                MovementKind.restock,
                qty,
                source_type="demo",
                source_id=f"seed:{code}",
                reason="opening stock",
                actor=actor,
            )
        db.session.commit()

        now = datetime.now(UTC)
        shows = []
        for day in (2, 1, 0):
            started = (now - timedelta(days=day, hours=2)).replace(
                minute=5, second=0, microsecond=0
            )
            show = Show(
                organization_id=org.id,
                show_key=started.isoformat(),
                started_at=started,
                show_date=show_date(started),
                source_url="/live/demo-card-shop",
            )
            db.session.add(show)
            db.session.flush()
            for i, (user, body) in enumerate(_lines(day), start=1):
                db.session.add(
                    ChatMessage(
                        show_id=show.id,
                        client_id=f"{show.id:04x}{i:012x}",
                        seq=i,
                        username=user,
                        body=body,
                        raw_text=f"{user} {body}".strip(),
                        captured_at=started + timedelta(seconds=40 * i),
                    )
                )
            db.session.flush()
            pipeline.process_show(show.id)
            if day == 0:
                show.updated_at = now  # live
            else:
                show.updated_at = started + timedelta(hours=2)
            shows.append(show)
            db.session.commit()

        # Reconcile the two past shows from "Whatnot's export" built from their own sales.
        from ..models import Sale, SaleStatus

        for show in shows[:2]:
            rows = ["Order ID,Buyer,Product,Quantity,Sold Price,Status"]
            for s in db.session.scalars(
                select(Sale).where(Sale.show_id == show.id, Sale.status == SaleStatus.reserved)
            ):
                status = "Refunded" if s.buyer_username == BUYERS[4] else "Paid"
                total = (s.price_cents or 0) * s.quantity / 100
                rows.append(
                    f"W{s.id},{s.buyer_username},{s.sku.product.name},{s.quantity},{total:.2f},{status}"
                )
            plan = reconcile.plan(show, reconcile.parse_csv("\n".join(rows) + "\n"))
            reconcile.apply(show, plan, actor=actor, user=owner, import_id=f"demo{show.id}")
            db.session.commit()

        # Fulfilment progress on the oldest show.
        oldest = shows[0]
        for i, o in enumerate(
            db.session.scalars(
                select(Order)
                .where(Order.show_id == oldest.id, Order.status == OrderStatus.paid)
                .order_by(Order.id)
            ).all()
        ):
            steps = [OrderStatus.picking, OrderStatus.packed, OrderStatus.shipped][: (i % 4)]
            for st in steps:
                orders.advance(
                    o,
                    st,
                    actor_label=owner.email,
                    tracking="1Z999AA10123456784" if st is OrderStatus.shipped else None,
                )
        db.session.commit()
        click.echo(f"seeded {org.name}: sign in as {DEMO_OWNER} (password in app/cli/seed_demo.py)")

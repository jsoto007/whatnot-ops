"""whatnot shows and orders

Revision ID: 4252fd215563
Revises: 24ba9f9f0cf2
Create Date: 2026-11-10 20:41:41.643408

Whatnot Seller API, shows and orders (W2): what Whatnot says about the shop's shows
(`whatnot_shows`) and orders (`whatnot_orders`, `whatnot_order_items`; the buyer's id and
username only, no address). A recording points at the Whatnot show it is a recording of
(`shows.whatnot_show_id`), a sale tied to a Whatnot order remembers when it was placed
(`sales.ordered_at`), and `whatnot_connections.orders_mode` says whether Whatnot's orders
are only compared ("shadow", the default) or applied as they arrive ("auto").

`sales` and `shows` are written on every win and every upload, so the ALTERs give up
after 5 s instead of queueing behind a long transaction; the deploy then fails and the
running instance keeps serving. Every new column is nullable or has a constant default:
no row is rewritten.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "4252fd215563"
down_revision = "24ba9f9f0cf2"
branch_labels = None
depends_on = None


FK = "fk_shows_whatnot_show_id"


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "whatnot_orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("whatnot_id", sa.String(length=20), nullable=False),
        sa.Column("whatnot_show_id", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("transaction_type", sa.String(length=16), nullable=True),
        sa.Column("buyer_id", sa.String(length=32), nullable=True),
        sa.Column("buyer_username", sa.String(length=80), nullable=True),
        sa.Column("subtotal_cents", sa.Integer(), nullable=True),
        sa.Column("shipping_cents", sa.Integer(), nullable=True),
        sa.Column("tax_cents", sa.Integer(), nullable=True),
        sa.Column("discount_cents", sa.Integer(), nullable=True),
        sa.Column("total_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("whatnot_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_orders_org_id"),
    )
    with op.batch_alter_table("whatnot_orders", schema=None) as batch_op:
        batch_op.create_index(
            "ix_whatnot_orders_org_buyer",
            ["organization_id", sa.literal_column("lower(buyer_username)")],
            unique=False,
        )
        batch_op.create_index(
            "ix_whatnot_orders_org_show", ["organization_id", "whatnot_show_id"], unique=False
        )
        batch_op.create_index(
            "ix_whatnot_orders_org_status",
            ["organization_id", "status", "ordered_at"],
            unique=False,
        )

    op.create_table(
        "whatnot_shows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("whatnot_id", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("orders_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_shows_org_id"),
    )
    with op.batch_alter_table("whatnot_shows", schema=None) as batch_op:
        batch_op.create_index(
            "ix_whatnot_shows_org_status", ["organization_id", "status"], unique=False
        )

    op.create_table(
        "whatnot_order_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("whatnot_id", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("sku_code", sa.String(length=80), nullable=True),
        sa.Column("product_id", sa.String(length=20), nullable=True),
        sa.Column("variant_id", sa.String(length=40), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=True),
        sa.Column("whatnot_shipment_id", sa.String(length=20), nullable=True),
        sa.Column("sale_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["whatnot_orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_order_items_org_id"),
    )
    with op.batch_alter_table("whatnot_order_items", schema=None) as batch_op:
        batch_op.create_index("ix_whatnot_order_items_order", ["order_id"], unique=False)
        batch_op.create_index("ix_whatnot_order_items_sale", ["sale_id"], unique=False)
        batch_op.create_index(
            "ix_whatnot_order_items_shipment",
            ["organization_id", "whatnot_shipment_id"],
            unique=False,
        )

    with op.batch_alter_table("sales", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ordered_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("shows", schema=None) as batch_op:
        batch_op.add_column(sa.Column("whatnot_show_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("whatnot_link", sa.String(length=8), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_shows_whatnot_show_id"), ["whatnot_show_id"], unique=False
        )
        batch_op.create_foreign_key(
            FK, "whatnot_shows", ["whatnot_show_id"], ["id"], ondelete="SET NULL"
        )

    with op.batch_alter_table("whatnot_connections", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("orders_mode", sa.String(length=8), server_default="shadow", nullable=False)
        )


def downgrade():
    with op.batch_alter_table("whatnot_connections", schema=None) as batch_op:
        batch_op.drop_column("orders_mode")

    with op.batch_alter_table("shows", schema=None) as batch_op:
        # WARNING: constraint name is None; this directive will fail as
        # rendered.  Add a name, or use a naming convention; see
        # https://alembic.sqlalchemy.org/en/latest/naming.html
        batch_op.drop_constraint(FK, type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_shows_whatnot_show_id"))
        batch_op.drop_column("whatnot_link")
        batch_op.drop_column("whatnot_show_id")

    with op.batch_alter_table("sales", schema=None) as batch_op:
        batch_op.drop_column("ordered_at")

    with op.batch_alter_table("whatnot_order_items", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_order_items_shipment")
        batch_op.drop_index("ix_whatnot_order_items_sale")
        batch_op.drop_index("ix_whatnot_order_items_order")

    op.drop_table("whatnot_order_items")
    with op.batch_alter_table("whatnot_shows", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_shows_org_status")

    op.drop_table("whatnot_shows")
    with op.batch_alter_table("whatnot_orders", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_orders_org_status")
        batch_op.drop_index("ix_whatnot_orders_org_show")
        batch_op.drop_index("ix_whatnot_orders_org_buyer")

    op.drop_table("whatnot_orders")

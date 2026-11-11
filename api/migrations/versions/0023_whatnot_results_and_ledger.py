"""whatnot results and ledger

Revision ID: 0e873ef2dd3a
Revises: e3eeeb2de895
Create Date: 2026-11-10 22:11:58.691271

Whatnot Seller API, profit and show results (W6): each show's results as Whatnot
calculates them (`whatnot_shows.analytics`), the reports the app asks Whatnot to build
(`whatnot_reports`) and the lines of its financial ledger (`whatnot_ledger`, unique on
Whatnot's own transaction id so a report imported twice changes nothing).

Two new tables and three nullable columns on `whatnot_shows` (a small table written only
when a show is read): no row is rewritten, and no table the live show writes to is touched.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0e873ef2dd3a"
down_revision = "e3eeeb2de895"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whatnot_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("whatnot_id", sa.String(length=40), nullable=False),
        sa.Column("report_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.Column("skipped", sa.Integer(), nullable=True),
        sa.Column("repeated", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_reports_org_id"),
    )
    with op.batch_alter_table("whatnot_reports", schema=None) as batch_op:
        batch_op.create_index(
            "ix_whatnot_reports_org_status", ["organization_id", "status"], unique=False
        )

    op.create_table(
        "whatnot_ledger",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("ledger_transaction_id", sa.String(length=64), nullable=False),
        sa.Column("transaction_type", sa.String(length=48), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("order_placed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("whatnot_order_id", sa.String(length=20), nullable=True),
        sa.Column("whatnot_show_id", sa.String(length=40), nullable=True),
        sa.Column("whatnot_shipment_id", sa.String(length=20), nullable=True),
        sa.Column("listing_title", sa.String(length=240), nullable=True),
        sa.Column("sku_code", sa.String(length=80), nullable=True),
        sa.Column("buy_format", sa.String(length=24), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("buyer_username", sa.String(length=80), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("buyer_paid_cents", sa.Integer(), nullable=True),
        sa.Column("item_price_cents", sa.Integer(), nullable=True),
        sa.Column("coupon_cents", sa.Integer(), nullable=True),
        sa.Column("seller_shipping_cents", sa.Integer(), nullable=True),
        sa.Column("commission_cents", sa.Integer(), nullable=True),
        sa.Column("commission_tax_cents", sa.Integer(), nullable=True),
        sa.Column("processing_fee_cents", sa.Integer(), nullable=True),
        sa.Column("processing_fee_tax_cents", sa.Integer(), nullable=True),
        sa.Column("cost_of_goods_cents", sa.Integer(), nullable=True),
        sa.Column("report_id", sa.Integer(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(["report_id"], ["whatnot_reports.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "ledger_transaction_id", name="uq_whatnot_ledger_org_txn"
        ),
    )
    with op.batch_alter_table("whatnot_ledger", schema=None) as batch_op:
        batch_op.create_index(
            "ix_whatnot_ledger_org_completed", ["organization_id", "completed_at"], unique=False
        )
        batch_op.create_index(
            "ix_whatnot_ledger_org_show", ["organization_id", "whatnot_show_id"], unique=False
        )

    with op.batch_alter_table("whatnot_shows", schema=None) as batch_op:
        batch_op.add_column(sa.Column("analytics", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("analytics_status", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("analytics_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table("whatnot_shows", schema=None) as batch_op:
        batch_op.drop_column("analytics_at")
        batch_op.drop_column("analytics_status")
        batch_op.drop_column("analytics")

    with op.batch_alter_table("whatnot_ledger", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_ledger_org_show")
        batch_op.drop_index("ix_whatnot_ledger_org_completed")

    op.drop_table("whatnot_ledger")
    with op.batch_alter_table("whatnot_reports", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_reports_org_status")

    op.drop_table("whatnot_reports")

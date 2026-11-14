"""sale items

Revision ID: 9c4e1f7a2b63
Revises: 5b1c7e2a9d04
Create Date: 2026-11-14 07:00:00

A sale's products as rows (services/sale_items.py), so a product can be changed any number
of times and a win can hold more than one. Every sale that has a product gets one row,
marked `first`: its stock stays in the ledger under the sale's own keys, exactly where it
is. No stock is moved and no sale is changed.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "9c4e1f7a2b63"
down_revision = "5b1c7e2a9d04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sale_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column(
            "sale_id", sa.Integer(), sa.ForeignKey("sales.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sale_items_sale", "sale_items", ["sale_id"])
    op.create_index("ix_sale_items_sku", "sale_items", ["sku_id"])
    op.execute(
        """
        INSERT INTO sale_items (organization_id, sale_id, sku_id, quantity, first, created_at)
        SELECT organization_id, id, sku_id, quantity, true, created_at
        FROM sales
        WHERE sku_id IS NOT NULL
        """
    )


def downgrade():
    op.drop_index("ix_sale_items_sku", table_name="sale_items")
    op.drop_index("ix_sale_items_sale", table_name="sale_items")
    op.drop_table("sale_items")

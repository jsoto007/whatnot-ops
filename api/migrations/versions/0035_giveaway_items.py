"""giveaway items

Revision ID: 7e3a9d5c1f28
Revises: 2d8f6a1c4e90
Create Date: 2026-11-14 15:00:00

The product a person chose for a giveaway Whatnot lists (services/giveaways.py): one open
row per giveaway, keyed by Whatnot's id of the order line. A new, empty table.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7e3a9d5c1f28"
down_revision = "2d8f6a1c4e90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "giveaway_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False
        ),
        sa.Column("whatnot_item_id", sa.String(length=20), nullable=False),
        sa.Column("sku_id", sa.Integer(), sa.ForeignKey("skus.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("assigned_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "uq_giveaway_items_open",
        "giveaway_items",
        ["organization_id", "whatnot_item_id"],
        unique=True,
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_index("ix_giveaway_items_sku", "giveaway_items", ["sku_id"])


def downgrade():
    # A row here is stock that left the shelf for a giveaway: dropping the table would
    # leave those movements with nothing to say what they were for.
    if op.get_bind().execute(sa.text("SELECT 1 FROM giveaway_items LIMIT 1")).first():
        raise RuntimeError(
            "giveaway_items holds products assigned to giveaways; take them off first."
        )
    op.drop_index("ix_giveaway_items_sku", table_name="giveaway_items")
    op.drop_index("uq_giveaway_items_open", table_name="giveaway_items")
    op.drop_table("giveaway_items")

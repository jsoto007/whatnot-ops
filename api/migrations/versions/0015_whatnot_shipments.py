"""whatnot shipments

Revision ID: cfcf87a66fdd
Revises: 036b095c3060
Create Date: 2026-11-02 18:33:10.984140

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "cfcf87a66fdd"
down_revision = "036b095c3060"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whatnot_shipments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("show_id", sa.Integer(), nullable=True),
        sa.Column("device_id", sa.Integer(), nullable=True),
        sa.Column("row_key", sa.String(length=64), nullable=False),
        sa.Column("recipient", sa.String(length=80), nullable=False),
        sa.Column("order_date_text", sa.String(length=80), nullable=False),
        sa.Column("ordered_on", sa.Date(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column("items_text", sa.String(length=1000), nullable=False),
        sa.Column("item_lines", sa.JSON(), nullable=True),
        sa.Column("value_cents", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("tracking", sa.String(length=120), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["device_id"],
            ["devices.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["show_id"],
            ["shows.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "row_key", name="uq_whatnot_shipments_org_key"),
    )
    with op.batch_alter_table("whatnot_shipments", schema=None) as batch_op:
        batch_op.create_index("ix_whatnot_shipments_show", ["show_id"], unique=False)


def downgrade():
    with op.batch_alter_table("whatnot_shipments", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_shipments_show")

    op.drop_table("whatnot_shipments")

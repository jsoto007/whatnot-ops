"""whatnot lots: the lots inside each Shipments row, and the show filter it was read under

Revision ID: 7d3a1c5e9b20
Revises: cfcf87a66fdd
Create Date: 2026-11-05 19:10:00

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7d3a1c5e9b20"
down_revision = "cfcf87a66fdd"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("whatnot_shipments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("show_label", sa.String(length=300), nullable=False, server_default="")
        )
    op.create_table(
        "whatnot_lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("show_id", sa.Integer(), nullable=True),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("whatnot_order_id", sa.String(length=20), nullable=False),
        sa.Column("recipient", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("lot_number", sa.Integer(), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("shipping_cents", sa.Integer(), nullable=True),
        sa.Column("sale_type", sa.String(length=80), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"]),
        sa.ForeignKeyConstraint(["shipment_id"], ["whatnot_shipments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "whatnot_order_id", name="uq_whatnot_lots_org_order"
        ),
    )
    with op.batch_alter_table("whatnot_lots", schema=None) as batch_op:
        batch_op.create_index("ix_whatnot_lots_show", ["show_id"], unique=False)
        batch_op.create_index("ix_whatnot_lots_shipment_id", ["shipment_id"], unique=False)


def downgrade():
    with op.batch_alter_table("whatnot_lots", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_lots_shipment_id")
        batch_op.drop_index("ix_whatnot_lots_show")
    op.drop_table("whatnot_lots")
    with op.batch_alter_table("whatnot_shipments", schema=None) as batch_op:
        batch_op.drop_column("show_label")

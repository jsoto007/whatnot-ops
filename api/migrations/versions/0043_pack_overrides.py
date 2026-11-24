"""pack overrides

Revision ID: 7d3e9a41c0b2
Revises: c210bb2a6355
Create Date: 2026-11-24 18:00:00

One new table, no data change (written by hand: `flask db migrate` reads the development
database):
- pack_overrides: a manager's "Mark ready to pack" on the Orders page for one buyer of one
  show (`order_board`). One per show and buyer (the buyer's name in lower case, no "@").
  `covered` lists what held the box when it was marked; anything else undoes the mark.
  It moves the buyer to Ready to pack; it changes no order, sale or stock. Deleted with its
  show (a made show folded into a recording, or a show the owner deleted).

A new empty table: no lock on any existing table beyond the foreign keys' brief ones. No
enum, so the downgrade only drops the table.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7d3e9a41c0b2"
down_revision = "c210bb2a6355"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pack_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("buyer_key", sa.String(length=80), nullable=False),
        sa.Column("covered", sa.JSON(), nullable=False),
        sa.Column("by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("show_id", "buyer_key", name="uq_pack_overrides_show_buyer"),
    )


def downgrade():
    op.drop_table("pack_overrides")

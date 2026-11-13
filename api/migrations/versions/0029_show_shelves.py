"""show shelves

Revision ID: 44f1f7f60c31
Revises: 025e32237664
Create Date: 2026-11-13 12:30:00

Where each product sits for one show ("B4" = shelf B, spot 4), set by a person before the
show when they want it (Javier's call, 2026-11-13). A winner picks by typing the spot in
chat; the layout lets Review suggest that product. A new empty table: nothing to rewrite.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "44f1f7f60c31"
down_revision = "025e32237664"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "show_shelves",
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=4), nullable=False),
        sa.Column("sku_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"]),
        sa.PrimaryKeyConstraint("show_id", "code"),
    )


def downgrade():
    op.drop_table("show_shelves")

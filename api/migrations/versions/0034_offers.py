"""offers

Revision ID: 2d8f6a1c4e90
Revises: 9c4e1f7a2b63
Create Date: 2026-11-14 12:00:00

Special offers (services/offers.py): the shop's list of trusted moderators, whose chat lines
may say a lot is a "two for one", and how many products a person allowed on one sale. Two
new columns with defaults: nothing to rewrite.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2d8f6a1c4e90"
down_revision = "9c4e1f7a2b63"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("trusted_mods", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("sales", sa.Column("products_allowed", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("sales", "products_allowed")
    op.drop_column("organizations", "trusted_mods")

"""auto pick

Revision ID: 5b1c7e2a9d04
Revises: 0691a156f15f
Create Date: 2026-11-14 05:00:00

The shop's switch for the pick worker (services/picks.py): whether a winner's shelf code
names the product with no person, ten minutes after the show ends. Off for every shop: the
owner switches it on after comparing a real show. A new column with a default: nothing to
rewrite.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "5b1c7e2a9d04"
down_revision = "0691a156f15f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("auto_pick", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    op.drop_column("organizations", "auto_pick")

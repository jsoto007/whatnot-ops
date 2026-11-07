"""sales by organization and lower-case buyer

Revision ID: 02c81044d69a
Revises: 7d3a1c5e9b20
Create Date: 2026-11-07 15:00:00.000000

The parser's "is this the same win?" checks (`pipeline._same_win_elsewhere`,
`_player_sales_near`, `_declined_win`) look a buyer up across the whole shop by
`lower(buyer_username)` (and price). No index had that expression, so every player win
walked every sale the shop ever made: 11 -> 35 ms per win from 0 to 40k past sales, and
a cold parse grew with the square of its own wins (architecture review 2026-11-07, #2).

Built CONCURRENTLY so a deploy during a live show does not block the pipeline's writes;
an INVALID leftover from an interrupted build is dropped first (as in 0013).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "02c81044d69a"
down_revision = "7d3a1c5e9b20"
branch_labels = None
depends_on = None

NAME = "ix_sales_org_lower_buyer"


def upgrade():
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        invalid = bind.execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": NAME},
        ).first()
        if invalid:
            op.drop_index(NAME, table_name="sales", postgresql_concurrently=True)
        op.create_index(
            NAME,
            "sales",
            ["organization_id", sa.text("lower(buyer_username)"), "price_cents"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade():
    with op.get_context().autocommit_block():
        op.drop_index(NAME, table_name="sales", postgresql_concurrently=True, if_exists=True)

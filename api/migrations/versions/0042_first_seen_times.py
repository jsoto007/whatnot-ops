"""first seen times

Revision ID: c210bb2a6355
Revises: 510b479901b4
Create Date: 2026-11-20 08:00:00

Two new nullable columns, no data change (written by hand: `flask db migrate` reads the
development database):
- whatnot_shows.live_seen_at: when the app saw a show it knew as not yet on air (scheduled)
  go live or paused on Whatnot. With no known start, a show's 12 hours count from it, never
  from an older scheduled time (services/whatnot/showend.py `began`).
- whatnot_orders.first_stored_at: when the app first stored the order. Never moved by a later
  read (`synced_at` is). An order Whatnot gives no time for is placed by it after a laptop's
  move (showend.py `laptop_word`).

Rows already stored keep NULL: the old reading applies to them. Nullable columns with no
default are a catalog change on Postgres: no table rewrite, a brief lock on each table. No
enum, so the downgrade only drops the columns.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c210bb2a6355"
down_revision = "510b479901b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "whatnot_shows", sa.Column("live_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "whatnot_orders", sa.Column("first_stored_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("whatnot_orders", "first_stored_at")
    op.drop_column("whatnot_shows", "live_seen_at")

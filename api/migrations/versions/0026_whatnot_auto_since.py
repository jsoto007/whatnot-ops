"""whatnot auto since

Revision ID: c3aaedb8edfc
Revises: 7800d889e1be
Create Date: 2026-11-12 01:16:51.869876

When a shop last chose "Apply as they arrive" for Whatnot's orders
(`whatnot_connections.auto_since`). A show that was over before then is never applied on
its own: found on the first live connection, where weeks of ended shows are read and their
orders keep changing for days.

One nullable column, no rewrite. A shop already on "auto" keeps following what it follows:
its line is drawn at the day it connected.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3aaedb8edfc"
down_revision = "7800d889e1be"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "whatnot_connections",
        sa.Column("auto_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE whatnot_connections SET auto_since = connected_at WHERE orders_mode = 'auto'"
    )


def downgrade():
    op.drop_column("whatnot_connections", "auto_since")

"""label printed at

Revision ID: 0691a156f15f
Revises: 07de6d404017
Create Date: 2026-11-13 21:00:00

When a package's label was last sent to a printer from the app, so "Print labels" for a show
skips the ones already printed (Javier's call, 2026-11-13). The time only: a label shows the
buyer's address and is never kept. A new nullable column: nothing to rewrite.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0691a156f15f"
down_revision = "07de6d404017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "whatnot_shipments",
        sa.Column("label_printed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("whatnot_shipments", "label_printed_at")

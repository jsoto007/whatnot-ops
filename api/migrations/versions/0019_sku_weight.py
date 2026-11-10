"""sku weight

Revision ID: e8efc83f07b8
Revises: 5b7e2c9d4a61
Create Date: 2026-11-10 00:30:23.075243

One unit's weight per SKU, in ounces to 0.01 oz (Inventory → item panel and Add new
item). Nullable with no default: every existing item reads "not weighed", and adding
the column only touches the catalog, no row rewrite.

`skus` is locked on every sale (reserve/commit), so the ALTER gives up after 5 s
instead of queueing behind a long transaction and stalling ingest; the deploy then
fails and the running instance keeps serving.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e8efc83f07b8"
down_revision = "5b7e2c9d4a61"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    with op.batch_alter_table("skus", schema=None) as batch_op:
        batch_op.add_column(sa.Column("weight_oz", sa.Numeric(precision=7, scale=2), nullable=True))


def downgrade():
    with op.batch_alter_table("skus", schema=None) as batch_op:
        batch_op.drop_column("weight_oz")

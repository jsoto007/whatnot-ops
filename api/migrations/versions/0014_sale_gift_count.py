"""sale gift_count

Revision ID: 036b095c3060
Revises: 3b7e1c9d0a41
Create Date: 2026-11-02 18:06:44.367848

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "036b095c3060"
down_revision = "3b7e1c9d0a41"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("sales", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("gift_count", sa.Integer(), server_default="0", nullable=False)
        )


def downgrade():
    with op.batch_alter_table("sales", schema=None) as batch_op:
        batch_op.drop_column("gift_count")

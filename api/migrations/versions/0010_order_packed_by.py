"""order packed_by

Revision ID: 5ee0541d1368
Revises: 44b1c20594cd
Create Date: 2026-10-24 10:54:04.697541

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "5ee0541d1368"
down_revision = "44b1c20594cd"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("packed_by", sa.String(length=80), nullable=True))


def downgrade():
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("packed_by")

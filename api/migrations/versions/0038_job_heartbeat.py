"""job heartbeat

Revision ID: ac0f44567c93
Revises: a10c7a72396f
Create Date: 2026-11-19 21:31:09

One nullable column, no data change: jobs.heartbeat_at, the last time a running job's
worker said it was alive (services/jobs.py). A job whose worker stops beating is given
back after about a minute and a half instead of fifteen. Rows running at deploy time have
none and keep the fifteen-minute rule on locked_at. Adding a nullable column without a
default is a catalog change only: no table rewrite, a brief lock on jobs.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ac0f44567c93"
down_revision = "a10c7a72396f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("jobs", "heartbeat_at")

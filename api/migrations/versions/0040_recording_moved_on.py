"""recording moved on

Revision ID: edc107be9f62
Revises: f0413b445125
Create Date: 2026-11-19 22:47:29

One new nullable column, no data change:
- shows.moved_on_at: when the server last heard from the show laptop that the show's page
  moved on to another channel (Whatnot moves a viewer on when a show ends). The server's own
  clock at the report. A hint that the show ended, used only by services/whatnot/showend.py
  when Whatnot gives no end time.

A nullable column with no default is a catalog change on Postgres: no table rewrite, a brief
lock on shows. No enum, so the downgrade only drops the column.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "edc107be9f62"
down_revision = "f0413b445125"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("shows", sa.Column("moved_on_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("shows", "moved_on_at")

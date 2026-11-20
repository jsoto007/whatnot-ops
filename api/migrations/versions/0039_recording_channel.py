"""recording channel

Revision ID: f0413b445125
Revises: ac0f44567c93
Create Date: 2026-11-19 22:07:47

Three new columns, no data change:
- shows.channel: the channel (the seller's Whatnot handle) the laptop read off the show's
  page when it started recording (services/ingest.py). Nullable: older recordings, pages
  where it could not be read and made shows have none.
- devices.other_channel_lines / other_channel_at: how many chat lines a laptop sent from
  another seller's show, refused and not stored, and when the last batch came. Only a count.

Adding a nullable column, or a NOT NULL one with a constant default, is a catalog change on
Postgres 11+: no table rewrite, a brief lock on each table. No enum, so the downgrade only
drops the columns.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f0413b445125"
down_revision = "ac0f44567c93"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "devices",
        sa.Column("other_channel_lines", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "devices", sa.Column("other_channel_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("shows", sa.Column("channel", sa.String(length=80), nullable=True))


def downgrade():
    op.drop_column("shows", "channel")
    op.drop_column("devices", "other_channel_at")
    op.drop_column("devices", "other_channel_lines")

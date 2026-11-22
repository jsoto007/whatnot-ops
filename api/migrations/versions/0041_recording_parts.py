"""recording parts

Revision ID: 510b479901b4
Revises: edc107be9f62
Create Date: 2026-11-19 23:47:06

One new nullable column and its index, no data change:
- shows.recording_key: on a part of a laptop recording that ran through two Whatnot shows
  (services/show_split.py), the laptop's own key for that recording. The laptop keeps
  uploading under that key; ingest stores each line in the part its capture time falls in.
  None on every other show.
- ix_shows_org_recording_key (organization_id, recording_key): ingest looks the parts up on
  every batch.

A nullable column with no default is a catalog change on Postgres (no table rewrite); the
index is built in the same short transaction (the shows table holds one row per recording).
No enum, so the downgrade only drops the index and the column.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "510b479901b4"
down_revision = "edc107be9f62"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("shows", sa.Column("recording_key", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_shows_org_recording_key", "shows", ["organization_id", "recording_key"], unique=False
    )


def downgrade():
    op.drop_index("ix_shows_org_recording_key", table_name="shows")
    op.drop_column("shows", "recording_key")

"""deleted recordings

Revision ID: a10c7a72396f
Revises: 80a51ddca3bc
Create Date: 2026-11-14 14:22:28

One small table, no data change: the key of each laptop recording whose data the owner
deleted (services/recordings.py), so a late upload of that recording is dropped instead
of making it again. Ingest looks a key up by the unique index on every batch.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a10c7a72396f"
down_revision = "80a51ddca3bc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deleted_recordings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("show_key", sa.String(length=64), nullable=False),
        sa.Column("deleted_by_id", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deleted_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "show_key", name="uq_deleted_recordings_org_key"),
    )


def downgrade():
    op.drop_table("deleted_recordings")

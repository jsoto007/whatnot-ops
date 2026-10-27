"""device extension version

Revision ID: 1085db4c6599
Revises: 750c5d03ab7f
Create Date: 2026-10-27 12:42:27.411819

The Chat Scraper version each laptop last called in with, so Settings → Laptops can
say which ones need an update. Null for laptops on a build before 1.5.0.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "1085db4c6599"
down_revision = "750c5d03ab7f"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("extension_version", sa.String(length=16), nullable=True))


def downgrade():
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_column("extension_version")

"""chat message source

Revision ID: 750c5d03ab7f
Revises: 5ee0541d1368
Create Date: 2026-10-24 18:14:57.247339

"chat" for a typed chat line (every existing row), "player" for a win the extension read
from Whatnot's video player. The server default fills existing rows without a rewrite.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "750c5d03ab7f"
down_revision = "5ee0541d1368"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(length=8), server_default="chat", nullable=False)
        )
        batch_op.create_check_constraint("ck_chat_messages_source", "source IN ('chat', 'player')")


def downgrade():
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_constraint("ck_chat_messages_source", type_="check")
        batch_op.drop_column("source")

"""query indexes

Revision ID: 80a51ddca3bc
Revises: 7e3a9d5c1f28
Create Date: 2026-11-14 11:20:37

Two indexes, no data change (claude-reports/Efficiency-Review, F19):
- chat_messages (username, captured_at): the parser's "the same chat line again" check
  (pipeline._same_chat_line_near) looks across the shop's shows by sender and time, and
  read the whole username index before (2.8 ms per line at 632k stored lines, growing).
- jobs (kind, payload ->> 'show_id'): a show's latest parse job, read on its page
  (routes/shows._stats), scanned every parse job kept (7 ms at 30k).
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "80a51ddca3bc"
down_revision = "7e3a9d5c1f28"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.create_index(
            "ix_chat_messages_username_captured", ["username", "captured_at"], unique=False
        )
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.create_index(
            "ix_jobs_kind_show",
            ["kind", sa.literal_column("(payload ->> 'show_id')")],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_kind_show")
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_index("ix_chat_messages_username_captured")

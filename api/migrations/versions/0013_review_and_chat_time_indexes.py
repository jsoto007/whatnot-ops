"""review and chat time indexes

Revision ID: 3b7e1c9d0a41
Revises: 1085db4c6599
Create Date: 2026-10-31 12:00:00.000000

Indexes for predicates that ran as sequential scans: open review items per show
(show page, dashboard, every parse run), review items by sale and by message (the
pipeline's "already flagged?" checks), and chat lines by show and capture time
(player-win duplicate window, a sale's surrounding chat, chat pace).

Built CONCURRENTLY so a deploy during a live show does not block ingest writes. A
concurrent build that was interrupted (a deploy killed mid-upgrade) leaves an INVALID
index that IF NOT EXISTS would then skip forever, so such leftovers are dropped first.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "3b7e1c9d0a41"
down_revision = "1085db4c6599"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_review_items_show_status", "review_items", ["show_id", "status"]),
    ("ix_review_items_sale_id", "review_items", ["sale_id"]),
    ("ix_review_items_message_id", "review_items", ["message_id"]),
    ("ix_chat_messages_show_captured", "chat_messages", ["show_id", "captured_at"]),
]


def upgrade():
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        for name, table, cols in INDEXES:
            invalid = bind.execute(
                sa.text(
                    "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = :name AND NOT i.indisvalid"
                ),
                {"name": name},
            ).first()
            if invalid:
                op.drop_index(name, table_name=table, postgresql_concurrently=True)
            op.create_index(name, table, cols, postgresql_concurrently=True, if_not_exists=True)


def downgrade():
    with op.get_context().autocommit_block():
        for name, table, _cols in INDEXES:
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)

"""chat lines saved under the avatar letter get their writer back

Revision ID: 025e32237664
Revises: 16d3b4c9ae25
Create Date: 2026-11-13 12:00:00

Whatnot draws a user with no profile photo as the first letter of their name, in its own
element before the name. Chat Scraper before 1.9.1 took that letter for the username, so
"L" "lani808 B4" was saved instead of "lani808" "B4" (shows of 2026-11-08 and
2026-11-13), and a winner's pick never reached their sale (services/sale_chat.py reads lines
by name).

Changed: a chat line whose name is one character and whose message starts with the name of
a winner of the same show beginning with that character, with more after it (the extension's
own rule, extension/src/capture/text.ts). Stored text has no element boundaries, so a real
one-letter username typing "<winner> B4" would look the same: limiting it to the show's
winners keeps the change to what it is for (their picks), and other lines keep the letter.
Raw text stays as captured. A second run changes nothing. Data only; the downgrade leaves the
lines as they are.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "025e32237664"
down_revision = "16d3b4c9ae25"
branch_labels = None
depends_on = None

# One statement, so tests/test_chat_avatar_migration.py runs exactly what production runs.
SQL = r"""
UPDATE chat_messages AS m
   SET username = split_part(m.body, ' ', 1),
       body = substr(m.body, length(split_part(m.body, ' ', 1)) + 2)
 WHERE m.source = 'chat'
   AND length(m.username) = 1
   AND m.body ~ '^[A-Za-z0-9._-]{2,40} [^ ]'
   AND lower(left(m.body, 1)) = lower(m.username)
   AND EXISTS (
       SELECT 1 FROM sales AS s
        WHERE s.show_id = m.show_id
          AND lower(s.buyer_username) = lower(split_part(m.body, ' ', 1))
   )
"""


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(SQL)


def downgrade():
    pass

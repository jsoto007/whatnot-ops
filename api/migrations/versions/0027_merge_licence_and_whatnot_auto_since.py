"""merge licence acceptances and whatnot auto since

Revision ID: 16d3b4c9ae25
Revises: fd0d60366112, c3aaedb8edfc
Create Date: 2026-11-12 15:25:52.443842

Two pull requests (60, licence acceptances, and 61, whatnot auto since) each added a 0026
revision on top of 0025 and were merged seconds apart, so the folder had two heads and a
plain `flask db upgrade` (the web service's start command) stopped with "Multiple head
revisions are present". This revision joins them into one head again.

No schema change. A database that already holds either 0026 gets the other one first and
then this; neither 0026 file is touched. `tests/test_migrations.py` fails the suite if
the folder ever has two heads again.
"""

# revision identifiers, used by Alembic.
revision = "16d3b4c9ae25"
down_revision = ("fd0d60366112", "c3aaedb8edfc")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

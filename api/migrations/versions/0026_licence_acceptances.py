"""licence acceptances

Revision ID: fd0d60366112
Revises: 7800d889e1be
Create Date: 2026-11-11 10:05:42.432532

The shop's click to accept the Sales Ops License and Data Terms (LICENSE.md §2.2): one
row per shop per licence version, with who clicked, when and from which address
(services/licence.py). Existing shops get no row, so their owners are asked on their
next sign-in.

One new table, nothing rewritten. Its foreign keys briefly lock `organizations` and
`users`; the lock timeout makes the migration give up rather than hold a live show.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "fd0d60366112"
down_revision = "7800d889e1be"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "licence_acceptances",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_ip", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_licence_acceptances_organization_id",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_licence_acceptances_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "version", name="uq_licence_acceptances_org_version"
        ),
    )


def downgrade():
    op.drop_table("licence_acceptances")

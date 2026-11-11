"""buyers

Revision ID: c5c3e315d4aa
Revises: 0e873ef2dd3a
Create Date: 2026-11-10 22:51:57.894320

Buyer profiles (W7): one row per person the shop knows something about (`buyers`: Whatnot's
user id when an order named it, the team's note and VIP / watch flag) and every username
that person has used (`buyer_names`, one holder per name). What a buyer bought is not
stored here; it is worked out from the sales by name.

Two new tables, nothing rewritten. Their foreign keys briefly lock `organizations` and
`users`; the lock timeout makes the migration give up rather than hold a live show.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c5c3e315d4aa"
down_revision = "0e873ef2dd3a"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "buyers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("whatnot_user_id", sa.String(length=32), nullable=True),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("username_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("flag", sa.String(length=8), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("noted_by_id", sa.Integer(), nullable=True),
        sa.Column("noted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["noted_by_id"], ["users.id"], name="fk_buyers_noted_by_id_users"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_buyers_organization_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "whatnot_user_id", name="uq_buyers_org_whatnot_user"
        ),
    )
    op.create_index("ix_buyers_org_flag", "buyers", ["organization_id", "flag"], unique=False)

    op.create_table(
        "buyer_names",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("buyer_id", sa.Integer(), nullable=False),
        sa.Column("name_lower", sa.String(length=80), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("since_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["buyer_id"], ["buyers.id"], name="fk_buyer_names_buyer_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_buyer_names_organization_id"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "name_lower", name="uq_buyer_names_org_name"),
    )
    op.create_index("ix_buyer_names_buyer", "buyer_names", ["buyer_id"], unique=False)


def downgrade():
    op.drop_index("ix_buyer_names_buyer", table_name="buyer_names")
    op.drop_table("buyer_names")
    op.drop_index("ix_buyers_org_flag", table_name="buyers")
    op.drop_table("buyers")

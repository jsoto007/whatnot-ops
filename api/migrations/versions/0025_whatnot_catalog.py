"""whatnot catalog

Revision ID: 7800d889e1be
Revises: c5c3e315d4aa
Create Date: 2026-11-11 00:40:00.000000

Whatnot Seller API, catalog link (W4): the shop's Whatnot listings as the API reports
them (`whatnot_variants`), each tied to the product here it is the same thing as.

One new table, nothing rewritten. Its foreign keys briefly lock `organizations`, `skus`
and `users`; the lock timeout makes the migration give up rather than hold a live show.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "7800d889e1be"
down_revision = "c5c3e315d4aa"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.create_table(
        "whatnot_variants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("whatnot_id", sa.String(length=40), nullable=False),
        sa.Column("product_id", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("options", sa.String(length=240), nullable=False),
        sa.Column("sku_code", sa.String(length=80), nullable=True),
        sa.Column("external_id", sa.String(length=120), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("available", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=True),
        sa.Column("sku_id", sa.Integer(), nullable=True),
        sa.Column("link", sa.String(length=8), nullable=True),
        sa.Column("linked_by_id", sa.Integer(), nullable=True),
        sa.Column("gone_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["linked_by_id"], ["users.id"], name="fk_whatnot_variants_linked_by_id"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_whatnot_variants_organization_id"
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["skus.id"], name="fk_whatnot_variants_sku_id", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "whatnot_id", name="uq_whatnot_variants_org_id"),
    )
    op.create_index(
        "ix_whatnot_variants_org_sku",
        "whatnot_variants",
        ["organization_id", "sku_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_whatnot_variants_org_sku", table_name="whatnot_variants")
    op.drop_table("whatnot_variants")

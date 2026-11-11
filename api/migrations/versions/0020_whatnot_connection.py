"""whatnot connection

Revision ID: 24ba9f9f0cf2
Revises: e8efc83f07b8
Create Date: 2026-11-10 20:28:34.003337

Whatnot Seller API, the foundation (W1): which Whatnot seller a shop is
(`whatnot_connections`; no token is stored, it comes from the environment), the signed
webhook deliveries received (`whatnot_events`, unique on Whatnot's webhook id so a retry
counts once) and how far each sync has read (`whatnot_sync_state`).

Three new tables and nothing else: no existing table is touched or locked.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "24ba9f9f0cf2"
down_revision = "e8efc83f07b8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "whatnot_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("webhook_id", sa.String(length=80), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("ref", sa.JSON(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=240), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("webhook_id", name="uq_whatnot_events_webhook_id"),
    )
    with op.batch_alter_table("whatnot_events", schema=None) as batch_op:
        batch_op.create_index(
            "ix_whatnot_events_org_received", ["organization_id", "received_at"], unique=False
        )
        batch_op.create_index("ix_whatnot_events_status", ["status", "received_at"], unique=False)

    op.create_table(
        "whatnot_sync_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("resource", sa.String(length=40), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=240), nullable=True),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "resource", name="uq_whatnot_sync_org_resource"),
    )
    op.create_table(
        "whatnot_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.String(length=32), nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("connected_by_id", sa.Integer(), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("problem", sa.String(length=32), nullable=True),
        sa.Column("problem_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["connected_by_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_whatnot_connections_org"),
        sa.UniqueConstraint("seller_id", name="uq_whatnot_connections_seller"),
    )


def downgrade():
    op.drop_table("whatnot_connections")
    op.drop_table("whatnot_sync_state")
    with op.batch_alter_table("whatnot_events", schema=None) as batch_op:
        batch_op.drop_index("ix_whatnot_events_status")
        batch_op.drop_index("ix_whatnot_events_org_received")

    op.drop_table("whatnot_events")

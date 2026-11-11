"""whatnot shipments from the api

Revision ID: e3eeeb2de895
Revises: 4252fd215563
Create Date: 2026-11-10 21:01:40.746933

Whatnot Seller API, shipments (W3): `whatnot_shipments` rows can now come from the API as
well as from the Seller Hub scraper. New, all nullable (or with a constant default): the
shipment's Whatnot id, its carrier status and tracking, whether a label exists (never the
label or its link), the drop-off date, what Whatnot wants done first, and when the app
asked for a label. A partial unique index keeps one row per Whatnot shipment.

`whatnot_shipments` is only written after a show, by one reader at a time; the ALTER
still gives up after 5 s rather than queue behind a long transaction.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e3eeeb2de895"
down_revision = "4252fd215563"
branch_labels = None
depends_on = None


FK = "fk_whatnot_shipments_label_requested_by"


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    with op.batch_alter_table("whatnot_shipments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("whatnot_id", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("api_status", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("carrier", sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column("tracking_code", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column("label_ready", sa.Boolean(), server_default="false", nullable=False)
        )
        batch_op.add_column(sa.Column("dropoff_due", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("required_actions", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("fulfillment_method", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("replaced_by", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("url", sa.String(length=512), nullable=True))
        batch_op.add_column(
            sa.Column("whatnot_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("label_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("label_requested_by_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("label_problem", sa.String(length=40), nullable=True))
        batch_op.create_index(
            "uq_whatnot_shipments_org_whatnot_id",
            ["organization_id", "whatnot_id"],
            unique=True,
            postgresql_where=sa.text("whatnot_id IS NOT NULL"),
        )
        batch_op.create_foreign_key(FK, "users", ["label_requested_by_id"], ["id"])


def downgrade():
    with op.batch_alter_table("whatnot_shipments", schema=None) as batch_op:
        # WARNING: constraint name is None; this directive will fail as
        # rendered.  Add a name, or use a naming convention; see
        # https://alembic.sqlalchemy.org/en/latest/naming.html
        batch_op.drop_constraint(FK, type_="foreignkey")
        batch_op.drop_index(
            "uq_whatnot_shipments_org_whatnot_id",
            postgresql_where=sa.text("whatnot_id IS NOT NULL"),
        )
        batch_op.drop_column("label_problem")
        batch_op.drop_column("label_requested_by_id")
        batch_op.drop_column("label_requested_at")
        batch_op.drop_column("whatnot_updated_at")
        batch_op.drop_column("url")
        batch_op.drop_column("replaced_by")
        batch_op.drop_column("fulfillment_method")
        batch_op.drop_column("required_actions")
        batch_op.drop_column("dropoff_due")
        batch_op.drop_column("label_ready")
        batch_op.drop_column("tracking_code")
        batch_op.drop_column("carrier")
        batch_op.drop_column("api_status")
        batch_op.drop_column("whatnot_id")

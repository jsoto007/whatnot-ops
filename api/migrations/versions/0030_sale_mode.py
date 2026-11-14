"""sale mode

Revision ID: 07de6d404017
Revises: 44f1f7f60c31
Create Date: 2026-11-13 19:30:00

How winners get their product: "winners_pick" (the winner names it in chat) or "listing" (the
listing is the product). The shop has a usual way, and a show can differ (Javier's call,
2026-11-13). Every shop that exists starts as "winners_pick": it is how the app has worked
so far, so nothing changes until someone chooses. `show_sale_modes` is a new empty table.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "07de6d404017"
down_revision = "44f1f7f60c31"
branch_labels = None
depends_on = None

MODES = "('winners_pick', 'listing')"


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("sale_mode", sa.String(length=16), nullable=False, server_default="winners_pick"),
    )
    op.create_check_constraint(
        "ck_organizations_sale_mode", "organizations", f"sale_mode IN {MODES}"
    )
    op.create_table(
        "show_sale_modes",
        sa.Column("show_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.CheckConstraint(f"mode IN {MODES}", name="ck_show_sale_modes_mode"),
        sa.ForeignKeyConstraint(["show_id"], ["shows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("show_id"),
    )


def downgrade():
    op.drop_table("show_sale_modes")
    op.drop_constraint("ck_organizations_sale_mode", "organizations", type_="check")
    op.drop_column("organizations", "sale_mode")

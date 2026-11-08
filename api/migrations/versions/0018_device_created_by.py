"""device created by

Revision ID: 5b7e2c9d4a61
Revises: 02c81044d69a
Create Date: 2026-11-07 18:00:00.000000

Who made each laptop key, so a key stops working when that person is disabled or
loses the right to manage laptops (security finding B1, 2026-11-07; open since the
2026-10-20 audit). `accounts._withdraw_grants` revokes the keys a person made.

Existing keys are linked where the database already says who made them: a paired
laptop from its pairing code's creator (the code row, or once retention has pruned
it, the `device.paired` -> `device.pairing_started` audit rows; the audit log is kept),
a key made in Settings from its `device.created` audit row. The rest stay NULL
("unknown" on the Laptops page). Nothing here revokes a key: a key made earlier by
someone already disabled or demoted keeps working and is flagged on the Laptops page.

`devices` is read on every ingest call, so the ALTER gives up after 5 s instead of
queueing behind a stuck transaction and stalling uploads; the deploy then fails and
the running instance keeps serving.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "5b7e2c9d4a61"
down_revision = "02c81044d69a"
branch_labels = None
depends_on = None

FK = "fk_devices_created_by_id_users"


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.add_column(sa.Column("created_by_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(FK, "users", ["created_by_id"], ["id"])

    # Paired laptops: the person who made the code. Only a user of the same shop.
    op.execute(
        """
        UPDATE devices d
           SET created_by_id = p.created_by_id
          FROM device_pairings p, users u
         WHERE p.device_id = d.id
           AND u.id = p.created_by_id
           AND u.organization_id = d.organization_id
           AND d.created_by_id IS NULL
        """
    )
    # Paired laptops whose code row was pruned: who started that pairing, per the audit log.
    op.execute(
        """
        UPDATE devices d
           SET created_by_id = s.actor_id
          FROM audit_log p, audit_log s, users u
         WHERE p.action = 'device.paired'
           AND p.target_type = 'device'
           AND p.target_id = d.id::text
           AND p.organization_id = d.organization_id
           AND s.action = 'device.pairing_started'
           AND s.actor_type = 'user'
           AND s.target_type = 'pairing'
           AND s.target_id = p.details ->> 'pairingId'
           AND s.organization_id = d.organization_id
           AND u.id = s.actor_id
           AND u.organization_id = d.organization_id
           AND d.created_by_id IS NULL
        """
    )
    # Keys made in Settings → Laptops: the person the audit log names.
    op.execute(
        """
        UPDATE devices d
           SET created_by_id = a.actor_id
          FROM audit_log a, users u
         WHERE a.action = 'device.created'
           AND a.actor_type = 'user'
           AND a.target_type = 'device'
           AND a.target_id = d.id::text
           AND a.organization_id = d.organization_id
           AND u.id = a.actor_id
           AND u.organization_id = d.organization_id
           AND d.created_by_id IS NULL
        """
    )


def downgrade():
    with op.batch_alter_table("devices", schema=None) as batch_op:
        batch_op.drop_constraint(FK, type_="foreignkey")
        batch_op.drop_column("created_by_id")

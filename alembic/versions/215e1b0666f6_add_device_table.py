"""Add device table

Revision ID: 215e1b0666f6
Revises: 795b535f8f0e
Create Date: 2023-04-09 22:48:19.479939

"""
from alembic import op
import sqlalchemy as sa
import uuid


# revision identifiers, used by Alembic.
revision = '215e1b0666f6'
down_revision = '795b535f8f0e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Note: a fake 0-device is added to all existent auth_sessions
    
    devices_table = op.create_table('devices',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('device_uuid', sa.Uuid(), nullable=False),
    sa.Column('onesignal_device_type', sa.Integer, nullable=False),
    sa.Column('extra_data', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_uuid')
    )
    op.bulk_insert(devices_table, [
        dict(id=0, device_uuid=uuid.UUID(int=0), onesignal_device_type=1, extra_data=dict(fake=True)),
    ])
    op.add_column('auth_sessions', sa.Column('device_id', sa.Integer(), nullable=False, server_default='0'))
    op.alter_column('auth_sessions', 'device_id', server_default=None)
    op.create_foreign_key(None, 'auth_sessions', 'devices', ['device_id'], ['id'])


def downgrade() -> None:
    op.drop_column('auth_sessions', 'device_id')
    op.drop_table('devices')

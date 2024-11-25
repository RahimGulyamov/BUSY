"""Rename Message to Command

Revision ID: f8e038ab4729
Revises: fb08cd0d744a
Create Date: 2023-04-01 14:27:06.174411

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f8e038ab4729'
down_revision = 'fb08cd0d744a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table('messages', 'commands')
    op.alter_column('commands', 'command', new_column_name='command_name')
    op.add_column('calls', sa.Column('finished', sa.Boolean(), nullable=False, server_default='TRUE'))
    op.alter_column('calls', 'finished', server_default=None)


def downgrade() -> None:
    op.drop_column('calls', 'finished')
    op.alter_column('commands', 'command_name', new_column_name='command')
    op.rename_table('commands', 'messages')

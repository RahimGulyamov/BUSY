"""Add tgmessage

Revision ID: 4a879fd52011
Revises: f8e038ab4729
Create Date: 2023-04-01 16:48:22.033603

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4a879fd52011'
down_revision = 'f8e038ab4729'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('tg_messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('tg_chat_id', sa.BigInteger(), nullable=False),
    sa.Column('tg_message_id', sa.BigInteger(), nullable=False),
    sa.Column('from_us', sa.Boolean(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('tg_messages')

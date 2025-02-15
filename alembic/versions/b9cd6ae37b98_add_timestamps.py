"""Add timestamps

Revision ID: b9cd6ae37b98
Revises: c86a28d1b7e4
Create Date: 2023-05-01 12:03:08.396284

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9cd6ae37b98'
down_revision = 'c86a28d1b7e4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('sms', sa.Column('timestamp', sa.DateTime(), nullable=True, server_default=sa.null()))
    op.alter_column('sms', 'timestamp', server_default=None)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('sms', 'timestamp')
    # ### end Alembic commands ###

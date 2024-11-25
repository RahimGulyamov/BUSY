"""added tg_message_id into calls table

Revision ID: 91cac04b696f
Revises: 9d78309f8d3b
Create Date: 2023-04-22 00:10:34.855456

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '91cac04b696f'
down_revision = '9d78309f8d3b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('calls', sa.Column('tg_message_id', sa.BigInteger(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('calls', 'tg_message_id')
    # ### end Alembic commands ###

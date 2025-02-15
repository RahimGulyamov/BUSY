"""add amo tokens

Revision ID: 000
Revises: 910113bde36e
Create Date: 2024-02-04 12:33:58.165134

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '000'
down_revision = '910113bde36e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('amo_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('refresh_token', sa.String(), nullable=False),
    sa.Column('access_token', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('amo_tokens')
    # ### end Alembic commands ###

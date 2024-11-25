"""Extra auto renewal field in user

Revision ID: 795b535f8f0e
Revises: 87e6e56c6186
Create Date: 2023-04-08 17:02:44.999665

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '795b535f8f0e'
down_revision = '87e6e56c6186'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('users', sa.Column('extra_plan_autocharge', sa.Boolean(), nullable=False))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('users', 'extra_plan_autocharge')
    # ### end Alembic commands ###

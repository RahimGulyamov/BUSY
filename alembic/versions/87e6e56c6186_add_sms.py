"""Add sms

Revision ID: 87e6e56c6186
Revises: fe2eb3b50738
Create Date: 2023-04-02 17:45:13.798002

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '87e6e56c6186'
down_revision = 'fe2eb3b50738'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('sms',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('is_incoming', sa.Boolean(), nullable=False),
    sa.Column('from_phone', sa.String(length=20), nullable=False),
    sa.Column('to_phone', sa.String(length=20), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('extra_data', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.alter_column('auth_codes', 'code',
               existing_type=sa.VARCHAR(length=20),
               type_=sa.String(length=6),
               existing_nullable=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('auth_codes', 'code',
               existing_type=sa.String(length=6),
               type_=sa.VARCHAR(length=20),
               existing_nullable=False)
    op.drop_table('sms')
    # ### end Alembic commands ###

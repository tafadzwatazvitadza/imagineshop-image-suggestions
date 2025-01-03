"""add user roles

Revision ID: a158fb791abf
Revises: b0ec255941e8
Create Date: 2024-12-29 10:41:44.098615

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a158fb791abf'
down_revision = 'b0ec255941e8'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('product_progress', schema=None) as batch_op:
        batch_op.add_column(sa.Column('completed_at', sa.DateTime(), nullable=True))

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'role',
                sa.String(length=50),
                nullable=False,
                server_default='worker'  # <--- Add default here
            )
        )

    # Optionally, after the column is created and populated, you can remove
    # the default if you only want it for the initial backfill:
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column('role', server_default=None)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('role')

    with op.batch_alter_table('product_progress', schema=None) as batch_op:
        batch_op.drop_column('completed_at')

    # ### end Alembic commands ###

from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    for table,column in [('projects',sa.Column('summary',sa.String(4000),nullable=False,server_default='')),
                         ('projects',sa.Column('deleted_at',sa.Float(),nullable=True)),
                         ('conversations',sa.Column('deleted_at',sa.Float(),nullable=True))]:
        if column.name not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}:
            op.add_column(table,column)

def downgrade():
    op.drop_column('conversations','deleted_at')
    op.drop_column('projects','deleted_at')
    op.drop_column('projects','summary')

"""Deleted projects must not reserve active project codes."""
from alembic import op
import sqlalchemy as sa
from knowledge_agent.db import Project
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    conn=op.get_bind()
    for constraint in sa.inspect(conn).get_unique_constraints('projects'):
        if constraint['column_names']==['code']:
            op.drop_constraint(constraint['name'],'projects',type_='unique')
    for index in Project.__table__.indexes:
        if index.name=='project_active_code':index.create(conn,checkfirst=True)

def downgrade():
    # Fails safely if codes have been reused; never discard project history.
    op.create_unique_constraint('projects_code_key','projects',['code'])
    op.drop_index('project_active_code',table_name='projects')

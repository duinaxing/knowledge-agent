"""Durable history titles, shared throttling and hot-path indexes."""
from alembic import op
import sqlalchemy as sa
from knowledge_agent.db import Base, RateLimit

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    conn=op.get_bind()
    if 'title' not in {c['name'] for c in sa.inspect(conn).get_columns('conversations')}:
        op.add_column('conversations',sa.Column('title',sa.String(100),nullable=False,server_default='新会话'))
    RateLimit.__table__.create(conn,checkfirst=True)
    for table in Base.metadata.tables.values():
        for index in table.indexes:
            index.create(conn,checkfirst=True)


def downgrade():
    op.drop_table('rate_limits')
    op.drop_column('conversations','title')

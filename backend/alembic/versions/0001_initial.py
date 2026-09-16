"""Initial business schema and PostgreSQL range constraint."""
from alembic import op
from sqlalchemy import text
from knowledge_agent.db import Base

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    conn.execute(text('CREATE EXTENSION IF NOT EXISTS btree_gist'))
    Base.metadata.create_all(conn)
    conn.execute(text('INSERT INTO system_state (id, epoch) VALUES (1, 1)'))
    conn.execute(text("""ALTER TABLE document_versions ADD CONSTRAINT version_no_overlap
        EXCLUDE USING gist (document_id WITH =,
        numrange(effective_from::numeric, effective_to::numeric, '[)') WITH &&)
        WHERE (publication = 'published') DEFERRABLE INITIALLY DEFERRED"""))


def downgrade():
    Base.metadata.drop_all(op.get_bind())

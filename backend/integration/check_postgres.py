"""Real PostgreSQL smoke; separate from the SQLite engineering suite."""
from sqlalchemy import select, func, text
from knowledge_agent.db import transaction, Project, Document, Version, Run, Conversation
from knowledge_agent.security import identity, epoch
from knowledge_agent.runs import submit
from knowledge_agent.schemas import Query
from knowledge_agent.worker import claim
from knowledge_agent.agent import build_graph
from knowledge_agent.config import settings
from langgraph.checkpoint.postgres import PostgresSaver

with transaction() as db:
    assert db.scalar(select(func.count()).select_from(Project))==6
    assert db.scalar(select(func.count()).select_from(Document))==60
    assert db.scalar(select(func.count()).select_from(Version).where(Version.publication=='published'))==60
    assert db.scalar(text("SELECT '[1,0]'::vector <=> '[1,0]'::vector"))==0
    c=Conversation(owner_id='u1',epoch=epoch(db));db.add(c);db.flush()
    run=submit(db,identity(db,'u1'),c.id,Query(message='test',client_request_id='ci'))
    id_=run.id
claimed=claim(Run)
assert claimed[0]==id_
with PostgresSaver.from_conn_string(settings.database_url.replace('postgresql+psycopg://','postgresql://')) as saver:
    saver.setup()
    assert build_graph(saver) is not None
print('PostgreSQL migrations, pgvector, synthetic seed, publication, lease and saver initialization passed.')

import time
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from knowledge_agent.db import engine,transaction,Run,Conversation
from knowledge_agent.runs import submit
from knowledge_agent.security import identity,epoch,AppError
from knowledge_agent.schemas import Query
from knowledge_agent.worker import claim

pytestmark=pytest.mark.skipif(engine.dialect.name!='postgresql',reason='Requires real PostgreSQL locking')


def test_database_rejects_overlapping_published_versions():
    from sqlalchemy import text
    from knowledge_agent.db import Version
    with engine.begin() as conn:
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS btree_gist'))
        conn.execute(text("""ALTER TABLE document_versions ADD CONSTRAINT version_no_overlap
          EXCLUDE USING gist (document_id WITH =,
          numrange(effective_from::numeric, effective_to::numeric, '[)') WITH &&)
          WHERE (publication = 'published') DEFERRABLE INITIALLY DEFERRED"""))
    with pytest.raises(IntegrityError):
        with transaction() as db:
            db.add(Version(id='overlap',document_id='d1',version_label='overlap',content_hash='x',
                path='unused',suffix='.md',processing='ready',publication='published',effective_from=100))
    # Adjacent half-open ranges must remain valid at the exact publication boundary.
    with transaction() as db:
        db.get(Version,'v1').effective_to=100
        db.add(Version(id='adjacent',document_id='d1',version_label='adjacent',content_hash='x',
            path='unused',suffix='.md',processing='ready',publication='published',effective_from=100))


def test_parallel_same_idempotency_key_one_run():
    with transaction() as db:
        c=Conversation(owner_id='u1',epoch=epoch(db));db.add(c);db.flush();id_=c.id
    barrier=threading.Barrier(5)
    def send(_):
        barrier.wait()
        with transaction() as db:
            return submit(db,identity(db,'u1'),id_,Query(message='test',client_request_id='same')).id
    with ThreadPoolExecutor(max_workers=5) as pool:ids=list(pool.map(send,range(5)))
    assert len(set(ids))==1


def test_parallel_different_requests_only_one_active():
    with transaction() as db:
        c=Conversation(owner_id='u1',epoch=epoch(db));db.add(c);db.flush();id_=c.id
    barrier=threading.Barrier(5)
    def send(i):
        barrier.wait()
        try:
            with transaction() as db:return submit(db,identity(db,'u1'),id_,Query(message='test',client_request_id=str(i))).id
        except AppError as e:return e.code
    with ThreadPoolExecutor(max_workers=5) as pool:outcomes=list(pool.map(send,range(5)))
    assert outcomes.count('CONVERSATION_BUSY')==4


def test_skip_locked_claims_unique_jobs():
    with transaction() as db:
        for i in range(5):
            c=Conversation(owner_id='u1',epoch=epoch(db));db.add(c);db.flush()
            submit(db,identity(db,'u1'),c.id,Query(message='test',client_request_id=str(i)))
    with ThreadPoolExecutor(max_workers=5) as pool:claimed=list(pool.map(lambda _:claim(Run),range(5)))
    assert all(claimed) and len({c[0] for c in claimed})==5


def test_process_crash_then_persistent_recovery(employee,monkeypatch,tmp_path):
    import subprocess
    import os
    import sys
    from pathlib import Path
    from langgraph.checkpoint.postgres import PostgresSaver
    from knowledge_agent.config import settings,ROOT
    from knowledge_agent.worker import sweep
    from knowledge_agent.agent import execute
    from test_runs_agent import create,scripted,tool_reply,final_fact
    _,id_=create(employee);_,gen=claim(Run)
    marker=tmp_path/'paused.txt'
    child_env=dict(os.environ, PYTHONPATH=str(ROOT/'backend/src'))
    child=subprocess.Popen([sys.executable,str(ROOT/'backend/integration/paused_run.py'),id_,str(gen),str(marker)], env=child_env)
    try:
        end=time.monotonic()+15
        while not marker.exists() and child.poll() is None and time.monotonic()<end:
            time.sleep(.05)
        assert marker.exists(), 'Child did not reach the reserved model call'
        child.terminate();child.wait(timeout=5)
    finally:
        if child.poll() is None:child.kill();child.wait(timeout=5)
    with transaction() as db:
        r=db.get(Run,id_);assert r.model_count==1;r.lease_until=time.time()-1
    sweep()
    assert employee.post('/api/runs/'+id_+'/resume').status_code==202
    _,gen2=claim(Run)
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P1'}),{'content':''},final_fact()])
    url=settings.database_url.replace('postgresql+psycopg://','postgresql://')
    with PostgresSaver.from_conn_string(url) as saver:
        execute(id_,gen2,saver)
    with PostgresSaver.from_conn_string(url) as reopened:
        snapshot=reopened.get_tuple({'configurable':{'thread_id':id_+':'+str(gen2)}})
        assert snapshot.checkpoint['channel_values']['answer']['status']=='answered'
    with transaction() as db:
        r=db.get(Run,id_);assert r.state=='completed' and r.model_count==4

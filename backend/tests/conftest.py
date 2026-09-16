import os
import tempfile
from pathlib import Path

TEST_DIR = Path(tempfile.mkdtemp(prefix='knowledge-tests-'))
test_database = os.environ.get('TEST_DATABASE_URL')
if test_database:
    from sqlalchemy.engine import make_url
    if not (make_url(test_database).database or '').startswith('knowledge_test_'):
        raise RuntimeError('TEST_DATABASE_URL must name an isolated knowledge_test_* database')
os.environ['DATABASE_URL'] = test_database or 'sqlite:///' + str(TEST_DIR / 'test.db')
os.environ['MODEL_MODE'] = 'test'
os.environ['EMBEDDING_DIM'] = '16'
os.environ['SEED_PASSWORD'] = 'test-password-2026'
os.environ['FILE_ROOT'] = str(TEST_DIR / 'files')

import pytest
from fastapi.testclient import TestClient
from knowledge_agent.db import Base, engine, transaction, User, Group, Membership, Project, Document, ACL, SystemState, Version, Chunk
from knowledge_agent.security import password_hash
from knowledge_agent.models import embed
from knowledge_agent.api import app, login_attempts
if engine.dialect.name == 'postgresql':
    from sqlalchemy import text
    with engine.begin() as connection:
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))


@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    login_attempts.clear()
    with transaction() as db:
        db.add(SystemState(id=1, epoch=1))
        for id_, role in [('u1','employee'),('u2','employee'),('admin','admin')]:
            db.add(User(id=id_, username=id_, display_name=id_, role=role, password_hash=password_hash('test-password-2026')))
        db.add(Group(id='g1', name='研发'))
        db.flush()
        db.add(Membership(user_id='u1', group_id='g1'))
        for id_ in ('p1','p2'):
            db.add(Project(id=id_, code=id_.upper(), name='北辰', phase='testing', health='delayed',
                           people=[{'display_name':'李工','role':'owner'}]))
        db.flush()
        db.add(ACL(resource_type='project', resource_id='p1', subject_type='group', subject_id='g1'))
        for id_, p in [('d1','p1'),('d2','p2')]:
            db.add(Document(id=id_, project_id=p, title='公开规范' if id_=='d1' else '秘密规范', org_visible=True))
        db.flush()
        for id_, doc, body in [('v1','d1','验收标准：P1 缺陷为零。合成资料。'),('v2','d2','超级机密：秘密发布计划。')]:
            db.add(Version(id=id_, document_id=doc, version_label='v1', content_hash='hash', path=id_, suffix='.md',
                           processing='ready', publication='published', effective_from=1,
                           embedding_model='TEST_ONLY_HASH', embedding_dim=16))
        db.flush()
        for id_, v, body in [('c1','v1','验收标准：P1 缺陷为零。合成资料。'),('c2','v2','超级机密：秘密发布计划。')]:
            db.add(Chunk(id=id_, version_id=v, ordinal=0,text=body,locator={'page':1,'offset':0}, embedding=embed([body])[0][0]))
    yield


@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client


def authenticate(client, name='u1'):
    client.headers['origin']='http://localhost:5173'
    r = client.post('/api/auth/login', json={'username':name,'password':'test-password-2026'})
    assert r.status_code == 200, r.text
    client.headers['x-csrf-token'] = r.json()['csrf']
    return client


@pytest.fixture
def employee(client):
    return authenticate(client)


@pytest.fixture
def administrator(client):
    return authenticate(client,'admin')

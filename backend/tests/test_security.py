import json
import time
import pytest
from sqlalchemy import select, delete
from knowledge_agent.db import transaction, User, Project, ACL, Membership, Document, Run, Version
from knowledge_agent.security import identity, require_read, AppError, bump_epoch
from knowledge_agent.retrieval import resolve_project, search
from knowledge_agent.schemas import Search
from conftest import authenticate


def test_et01_identity_extra_fields_rejected(client):
    client.headers['origin']='http://localhost:5173'
    r=client.post('/api/auth/login', json={'username':'u1','password':'test-password-2026','role':'admin'})
    assert r.status_code==422


def test_et02_employee_cannot_write(employee):
    assert employee.post('/api/admin/projects',json={'code':'X','name':'x'}).status_code==403
    with transaction() as db:
        assert db.scalar(select(Project.id).where(Project.code=='X')) is None


def test_et03_invisible_equals_absent():
    with transaction() as db:
        who=identity(db,'u1')
        for id_ in ['p2','nonexistent']:
            with pytest.raises(AppError) as e: require_read(db,who,'project',id_)
            assert (e.value.code,e.value.status)==('NOT_FOUND_OR_FORBIDDEN',404)


def test_et04_acl_intersection():
    with transaction() as db:
        who=identity(db,'u1')
        with pytest.raises(AppError):require_read(db,who,'document','d2')
        db.get(Document,'d1').org_visible=False
        db.flush()
        with pytest.raises(AppError):require_read(db,who,'document','d1')


def test_et05_authorized_ambiguity():
    with transaction() as db:
        db.get(Project,'p2').org_visible=True
        db.flush()
        p,choices=resolve_project(db,identity(db,'u1'),'北辰')
        assert p is None and len(choices)==2


def test_et06_hidden_candidate():
    with transaction() as db:
        p,choices=resolve_project(db,identity(db,'u1'),'北辰')
        assert p.id=='p1' and not choices


def test_et07_cross_user_resources(employee):
    conv=employee.post('/api/conversations').json()['id']
    run=employee.post(f'/api/conversations/{conv}/queries',json={'message':'hi','client_request_id':'k'}).json()['run_id']
    authenticate(employee,'u2')
    for url in [f'/api/conversations/{conv}/messages',f'/api/runs/{run}',f'/api/runs/{run}/events']:
        assert employee.get(url).status_code==404


@pytest.mark.parametrize('path',['d1/versions/v2/chunks/c1','d1/versions/v1/chunks/c2','d2/versions/v2/chunks/c2'])
def test_et08_tampered_excerpt(employee,path):
    r=employee.get('/api/documents/'+path)
    assert r.status_code==404 and '秘密' not in r.text


def test_et09_filter_before_embedding(monkeypatch):
    from knowledge_agent import models
    original=models.embed
    seen=[]
    monkeypatch.setattr(models,'rerank',lambda query, evidence, timeout: seen.extend(evidence) or evidence)
    with transaction() as db:
        result,_=search(db,identity(db,'u1'),Search(query='验收'),1)
        assert {r['document_id'] for r in result}=={'d1'}
    assert all('机密' not in e['text'] for e in seen)


def test_et10_owner_history_survives_epoch_change(employee):
    conv=employee.post('/api/conversations').json()['id']
    employee.post(f'/api/conversations/{conv}/queries',json={'message':'粘贴的机密','client_request_id':'k'})
    with transaction() as db:bump_epoch(db)
    r=employee.get(f'/api/conversations/{conv}/messages')
    assert r.json()['knowledge_updated'] and r.json()['messages'][0]['message']=='粘贴的机密'


def test_et11_membership_revocation(employee):
    with transaction() as db:
        db.execute(delete(Membership).where(Membership.user_id=='u1'))
        bump_epoch(db)
    assert employee.get('/api/projects').json()==[]


def test_et12_epoch_blocks_run_and_sse(employee):
    conv=employee.post('/api/conversations').json()['id']
    run=employee.post(f'/api/conversations/{conv}/queries',json={'message':'secret','client_request_id':'k'}).json()['run_id']
    with transaction() as db:bump_epoch(db)
    assert employee.get(f'/api/runs/{run}').status_code==409
    assert employee.get(f'/api/runs/{run}/events').status_code==409


def test_csrf_rejected(employee):
    employee.headers['origin']='https://evil.example'
    assert employee.post('/api/conversations').status_code==403


def test_logout_invalidates(employee):
    assert employee.post('/api/auth/logout').status_code==200
    assert employee.get('/api/me').status_code==401


def test_disabled_account_invalidates_session(employee):
    with transaction() as db:db.get(User,'u1').disabled=True
    assert employee.get('/api/me').status_code==401


def test_password_is_hashed():
    with transaction() as db:
        assert 'test-password' not in db.get(User,'u1').password_hash


def test_login_rate_limit(client,monkeypatch):
    from knowledge_agent import accounts
    monkeypatch.setattr(accounts.time,'time',lambda:1800000000.0)
    client.headers['origin']='http://localhost:5173'
    for _ in range(30):client.post('/api/auth/login',json={'username':'nobody','password':'x'})
    assert client.post('/api/auth/login',json={'username':'nobody','password':'x'}).status_code==429


def test_project_people_revision(administrator):
    p=administrator.get('/api/admin/projects').json()[0]
    body={k:v for k,v in p.items() if k not in ('id','updated_at')}
    body['people']=[{'display_name':'新负责人','role':'owner'}]
    r=administrator.patch('/api/admin/projects/p1',json=body)
    assert r.status_code==200 and r.json()['revision']==p['revision']+1


def test_acl_changes_epoch(administrator):
    r=administrator.put('/api/admin/resources/project/p1/acl',json={'org_visible':False,'grants':[]})
    assert r.status_code==200
    authenticate(administrator,'u1')
    assert administrator.get('/api/projects').json()==[]

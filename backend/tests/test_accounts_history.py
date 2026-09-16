import time
from fastapi.testclient import TestClient
from sqlalchemy import select, delete
from knowledge_agent.api import app
from knowledge_agent.db import transaction, User, Run, Conversation, ACL, Membership
from knowledge_agent.security import bump_epoch, password_ok, identity, can_read, read_filter
from knowledge_agent.worker import work_one
from knowledge_agent.retrieval import lexical_index, search
from knowledge_agent.schemas import Search
from knowledge_agent.db import Document,Project
from test_runs_agent import create,scripted,tool_reply,final_fact
from conftest import authenticate


def test_registration_is_employee_and_no_admin_access(client):
    client.headers['origin']='http://localhost:5173'
    body={'username':'new_user','display_name':'新用户','password':'123456'}
    assert client.post('/api/auth/register',json={**body,'role':'admin'}).status_code==422
    assert client.post('/api/auth/register',json=body).status_code==201
    assert client.post('/api/auth/register',json=body).status_code==409
    assert client.post('/api/auth/register',json={**body,'username':'admin'}).status_code==409
    login=client.post('/api/auth/login',json={'username':'new_user','password':'123456'})
    assert login.status_code==200 and login.json()['role']=='employee'
    client.headers['x-csrf-token']=login.json()['csrf']
    for path in ['documents','projects','users','groups','jobs','feedback','metrics']:
        assert client.get('/api/admin/'+path).status_code==403
    assert client.post('/api/admin/documents',data={'title':'bad'},files={'file':('x.txt',b'bad')}).status_code==403
    with transaction() as db:
        u=db.scalar(select(User).where(User.username=='new_user'))
        assert u.password_hash!='123456' and password_ok('123456',u.password_hash)


def test_password_change_revokes_all_sessions_and_keeps_history(employee):
    conv,_=create(employee,'history')
    with TestClient(app) as other:
        authenticate(other)
        assert employee.post('/api/auth/password',json={'current_password':'bad','new_password':'654321'}).status_code==400
        assert employee.post('/api/auth/password',json={'current_password':'test-password-2026','new_password':'654321'}).status_code==200
        assert other.get('/api/me').status_code==401
        assert employee.get('/api/me').status_code==401
        assert employee.post('/api/auth/login',json={'username':'u1','password':'test-password-2026'}).status_code==401
        result=employee.post('/api/auth/login',json={'username':'u1','password':'654321'})
        assert result.status_code==200
        assert employee.get('/api/conversations/'+conv+'/messages').json()['messages'][0]['message']=='history'


def test_history_rechecks_revoked_sources_and_other_user(employee,monkeypatch):
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P1'}),{'content':''},final_fact()])
    conv,rid=create(employee);work_one(Run)
    with transaction() as db:bump_epoch(db)
    response=employee.get('/api/conversations/'+conv+'/messages').json()
    assert response['messages'][0]['answer']['evidence']
    with transaction() as db:
        db.execute(delete(Membership).where(Membership.user_id=='u1'));bump_epoch(db)
    response=employee.get('/api/conversations/'+conv+'/messages').json()
    assert not response['messages'][0]['answer']['evidence']
    assert '李工' not in response['messages'][0]['answer']['answer']
    with TestClient(app) as other:
        authenticate(other,'u2')
        assert other.get('/api/conversations/'+conv+'/messages').status_code==404
        assert other.patch('/api/conversations/'+conv,json={'title':'steal'}).status_code==404


def test_history_pagination_and_continue_after_update(employee):
    conv,rid=create(employee,'first')
    with transaction() as db:
        db.get(Run,rid).state='completed'
        for i in range(65):
            db.add(Run(owner_id='u1',conversation_id=conv,client_request_id=str(i),message=str(i),
                       epoch=1,state='completed',deadline=time.time(),created_at=time.time()+i))
        bump_epoch(db)
    page1=employee.get('/api/conversations/'+conv+'/messages').json()
    page2=employee.get('/api/conversations/'+conv+'/messages?offset=50').json()
    assert len(page1['messages'])==50 and page1['has_more']
    assert len(page2['messages'])==16 and not page2['has_more']
    assert not {r['id'] for r in page1['messages']}&{r['id'] for r in page2['messages']}
    assert employee.patch('/api/conversations/'+conv,json={'title':'我的资料'}).status_code==200
    assert employee.get('/api/conversations').json()[0]['title']=='我的资料'
    assert employee.post('/api/conversations/'+conv+'/queries',json={'message':'new','client_request_id':'new'}).status_code==202


def test_sql_authorization_matches_reference():
    with transaction() as db:
        for user in ['u1','u2','admin']:
            who=identity(db,user)
            for kind,model in [('project',Project),('document',Document)]:
                expected={r.id for r in db.scalars(select(model)) if can_read(db,who,kind,r)}
                assert set(db.scalars(select(model.id).where(read_filter(who,kind,model))))==expected


def test_lexical_cache_reused_without_permission_leak():
    lexical_index.cache_clear()
    with transaction() as db:
        search(db,identity(db,'u1'),Search(query='验收'),1,keyword_only=True)
        search(db,identity(db,'u1'),Search(query='验收'),1,keyword_only=True)
        assert lexical_index.cache_info().hits>=1
        assert search(db,identity(db,'u2'),Search(query='验收'),1,keyword_only=True)[0]==[]


def test_admin_operational_metrics(administrator):
    response=administrator.get('/api/admin/metrics')
    assert response.status_code==200
    assert response.json()['worker_concurrency']>=1


def test_context_limit_stops_before_model(employee,monkeypatch):
    from knowledge_agent.agent import ask_model
    from knowledge_agent.config import settings
    from knowledge_agent.security import AppError
    import pytest
    monkeypatch.setattr(settings,'max_context_bytes',8000)
    with pytest.raises(AppError) as error:
        ask_model({'messages':[{'role':'user','content':'x'*9000}]})
    assert error.value.code=='CONTEXT_BUDGET_EXCEEDED'

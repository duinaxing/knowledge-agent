from sqlalchemy import select
from knowledge_agent.db import transaction,Run,Project,Conversation
from knowledge_agent.worker import work_one,claim
from knowledge_agent.security import AppError
from knowledge_agent.runs import leased
from test_runs_agent import create,scripted,tool_reply,final_fact
import json
import pytest

def test_general_answer_is_not_cited(employee,monkeypatch):
    scripted(monkeypatch,[{'content':json.dumps({'intent':'general','answer':'Python 列表可以使用 append 添加元素。'})}])
    _,id_=create(employee,'Python 如何向列表添加元素？');work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert answer['kind']=='general' and not answer['evidence'] and 'append' in answer['answer']

def test_rag_still_cites_source(employee,monkeypatch):
    scripted(monkeypatch,[tool_reply('search_documents',{'query':'验收','project_id':'p1'}),{'content':''},final_fact('验收要求为 P1 缺陷为零。')])
    _,id_=create(employee,'P1验收要求？');work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert answer['evidence'] and answer.get('kind')!='general'

def test_delete_active_conversation_fences_worker(employee):
    conv,id_=create(employee);_,gen=claim(Run)
    assert employee.delete('/api/conversations/'+conv).status_code==200
    assert employee.get('/api/conversations').json()==[]
    assert employee.get('/api/conversations/'+conv+'/messages').status_code==404
    assert employee.get('/api/runs/'+id_).status_code==404
    with transaction() as db:
        assert db.get(Run,id_).message=='[已删除]'
        with pytest.raises(AppError):leased(db,id_,gen)

def test_cannot_delete_other_users_conversation(employee):
    with transaction() as db:
        c=Conversation(owner_id='u2',epoch=1);db.add(c);db.flush();id_=c.id
    assert employee.delete('/api/conversations/'+id_).status_code==404

def test_summary_and_project_delete(administrator):
    projects=administrator.get('/api/admin/projects').json()
    p=next(p for p in projects if p['id']=='p1')
    body={k:v for k,v in p.items() if k not in ('id','updated_at')}
    body['summary']='建设内部知识检索系统'
    r=administrator.patch('/api/admin/projects/p1',json=body)
    assert r.status_code==200 and r.json()['summary']==body['summary']
    assert administrator.delete('/api/admin/projects/p1').status_code==200
    assert 'p1' not in [p['id'] for p in administrator.get('/api/projects').json()]
    # Documents survive deletion and remain inspectable by the administrator.
    assert administrator.get('/api/admin/documents/d1/versions/v1/content').status_code==200

def test_employee_cannot_delete_project(employee):
    assert employee.delete('/api/admin/projects/p1').status_code==403

def test_project_code_does_not_match_suffix(employee,monkeypatch):
    with transaction() as db:
        db.add(Project(id='short',code='1',name='其他项目',org_visible=True))
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P1'}),{'content':''},final_fact()])
    _,id_=create(employee,'P1 的负责人是谁？');work_one(Run)
    assert employee.get('/api/runs/'+id_).json()['answer']['status']=='answered'

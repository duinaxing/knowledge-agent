import json
import time
import pytest
from sqlalchemy import select, func
from knowledge_agent.db import transaction, Run, Conversation, Project, RunEvent, Job, Version
from knowledge_agent.security import AppError, bump_epoch, identity
from knowledge_agent.worker import claim, sweep, work_one
from knowledge_agent.runs import reserve, leased
from knowledge_agent.agent import execute, execute_tool, validate_evidence, TOOLS
from knowledge_agent.retrieval import project_evidence
from knowledge_agent.config import settings
from conftest import authenticate


def create(employee, message='P1 的负责人是谁？', key='k', conv=None):
    conv=conv or employee.post('/api/conversations').json()['id']
    result=employee.post(f'/api/conversations/{conv}/queries',json={'message':message,'client_request_id':key})
    assert result.status_code==202,result.text
    return conv,result.json()['run_id']


def tool_reply(name,args):
    return {'content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}


def scripted(monkeypatch,replies):
    from knowledge_agent import models
    iterator=iter(replies)
    def chat(*args,**kwargs):return next(iterator),{'type':'generation','model':'TEST_SCRIPT','usage':None}
    monkeypatch.setattr(models,'chat',chat)


def final_fact(text='当前负责人为李工。',evidence='E1'):
    return {'content':json.dumps({'status':'answered','facts':[{'text':text,'evidence_ids':[evidence]}],'warnings':[]})}


def test_et21_unknown_write_tool(employee):
    _,id_=create(employee);_,gen=claim(Run)
    assert {t['function']['name'] for t in TOOLS}=={'search_documents','get_project_status','get_project_owner','get_document_excerpt'}
    with pytest.raises(AppError) as e:execute_tool({'run_id':id_,'generation':gen,'evidence':[]},'execute_sql',{'sql':'delete all'})
    assert e.value.code=='UNKNOWN_TOOL'


def test_unretrieved_excerpt_rejected(employee):
    _,id_=create(employee);_,gen=claim(Run)
    with pytest.raises(AppError) as e:execute_tool({'run_id':id_,'generation':gen,'evidence':[]},'get_document_excerpt',{'document_id':'d1','version_id':'v1','chunk_id':'c1'})
    assert e.value.code=='NOT_FOUND_OR_FORBIDDEN'


def test_et22_partial_on_status_failure(employee,monkeypatch):
    from knowledge_agent import agent
    original=agent.execute_tool
    def fail_status(state,name,args):
        if name=='get_project_status':raise AppError('TIMEOUT')
        return original(state,name,args)
    monkeypatch.setattr(agent,'execute_tool',fail_status)
    scripted(monkeypatch,[tool_reply('get_project_status',{'project_ref':'P1'}),
        tool_reply('search_documents',{'query':'验收','project_id':'p1'}),{'content':''},
        final_fact('验收标准为 P1 缺陷为零。')])
    _,id_=create(employee);assert work_one(Run)
    r=employee.get('/api/runs/'+id_).json()
    assert r['answer']['status']=='partial' and 'TIMEOUT' in r['answer']['warnings']


def test_et23_no_evidence_no_invented_reason(employee,monkeypatch):
    scripted(monkeypatch,[{'content':'原因是人手不足'}])
    _,id_=create(employee,'P1 为什么延期？');work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert answer['status']=='no_answer' and '人手不足' not in answer['answer']


def test_et24_current_project_evidence_has_revision():
    with transaction() as db:
        e=project_evidence(db.get(Project,'p1'),'project_status')
        assert e['health']=='delayed' and e['revision']==1 and e['updated_at']
        db.get(Project,'p1').revision+=1;db.flush()
        with pytest.raises(AppError):validate_evidence(db,identity(db,'u1'),[e])


def test_et25_idempotent_request(employee):
    conv,id_=create(employee)
    assert create(employee,conv=conv)[1]==id_
    with transaction() as db:assert db.scalar(select(func.count()).select_from(Run))==1


def test_et26_conflicting_requests(employee):
    conv,_=create(employee)
    for body in [{'message':'changed','client_request_id':'k'},{'message':'hi','client_request_id':'new'}]:
        assert employee.post(f'/api/conversations/{conv}/queries',json=body).status_code==409


def test_et27_sse_replay_sequence(employee,monkeypatch):
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P1'}),{'content':''},final_fact()])
    _,id_=create(employee);work_one(Run)
    r=employee.get('/api/runs/'+id_+'/events',headers={'Last-Event-ID':'1'})
    assert 'id: 1\n' not in r.text and 'event: final' in r.text
    assert '李工' not in r.text
    assert employee.get('/api/runs/'+id_).json()['answer']['status']=='answered'


def test_et28_lease_fencing_and_budget(employee):
    _,id_=create(employee);_,gen=claim(Run)
    reserve(id_,gen,'tool','same')
    with transaction() as db:db.get(Run,id_).lease_until=time.time()-1
    sweep()
    assert employee.post('/api/runs/'+id_+'/resume').status_code==202
    _,gen2=claim(Run)
    assert gen2>gen
    with transaction() as db:
        with pytest.raises(AppError):leased(db,id_,gen)
        assert db.get(Run,id_).tool_count==1


def test_et29_expired_run_fails(employee):
    conv,id_=create(employee)
    with transaction() as db:db.get(Run,id_).deadline=time.time()-1
    sweep()
    assert employee.get('/api/runs/'+id_).json()['error_code']=='DEADLINE_EXCEEDED'
    assert create(employee,key='new',conv=conv)[1]!=id_


def test_et30_repeated_tool_limit(employee):
    _,id_=create(employee);_,gen=claim(Run)
    reserve(id_,gen,'tool','same');reserve(id_,gen,'tool','same')
    with pytest.raises(AppError) as e:reserve(id_,gen,'tool','same')
    assert e.value.code=='REPEATED_CALL_LIMIT'


def test_model_budget_persists(employee):
    _,id_=create(employee);_,gen=claim(Run)
    for _ in range(settings.max_models):reserve(id_,gen,'model')
    with pytest.raises(AppError):reserve(id_,gen,'model')


def test_et31_switch_project(employee,monkeypatch):
    with transaction() as db:db.get(Project,'p2').org_visible=True
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P1'}),{'content':''},final_fact(),
                         tool_reply('get_project_owner',{'project_ref':'P2'}),{'content':''},final_fact()])
    conv,_=create(employee);work_one(Run)
    create(employee,'P2 的负责人是谁？','next',conv);work_one(Run)
    with transaction() as db:assert db.get(Conversation,conv).project_id=='p2'


def test_et32_feedback_ownership_and_admin_privacy(employee,monkeypatch):
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P1'}),{'content':''},final_fact()])
    conv,id_=create(employee);work_one(Run)
    assert employee.post('/api/answers/'+id_+'/feedback',json={'type':'citation','note':'请复核'}).status_code==201
    authenticate(employee,'u2')
    assert employee.post('/api/answers/'+id_+'/feedback',json={'type':'citation'}).status_code==404
    authenticate(employee,'admin')
    result=employee.get('/api/admin/feedback').json()
    assert result[0]['answer'] and 'message' not in result[0]
    assert employee.get('/api/conversations/'+conv+'/messages').status_code==404
    with transaction() as db:bump_epoch(db)
    result=employee.get('/api/admin/feedback').json()
    assert result[0]['answer'] is None and '请复核' not in json.dumps(result,ensure_ascii=False)


def test_revocation_during_model_call_blocks_answer(employee,monkeypatch):
    from knowledge_agent import models
    def revoked(*a,**kw):
        with transaction() as db:bump_epoch(db)
        return tool_reply('get_project_owner',{'project_ref':'P1'}),{}
    monkeypatch.setattr(models,'chat',revoked)
    _,id_=create(employee);work_one(Run)
    with transaction() as db:
        run=db.get(Run,id_)
        assert run.answer is None and run.error_code=='CONTEXT_CHANGED'


def test_invalid_citation_not_rendered(employee,monkeypatch):
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P1'}),{'content':''},final_fact('伪造结论','E999')])
    _,id_=create(employee);work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert answer['status']=='partial' and '伪造结论' not in answer['answer']


def test_invalid_json_terminates(employee,monkeypatch):
    scripted(monkeypatch,[{'tool_calls':[{'function':{'name':'get_project_status','arguments':'bad json'}}]}])
    _,id_=create(employee);work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert answer['status']=='failed' and 'INVALID_MODEL_RESPONSE' in answer['warnings']

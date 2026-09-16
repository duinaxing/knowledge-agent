"""Adversarial regressions: use only isolated fixture data and mocked providers."""
import json
import pytest
from sqlalchemy import delete,select
from knowledge_agent.db import transaction,Run,Membership,Group,Document,Version
from knowledge_agent.security import bump_epoch
from knowledge_agent.worker import work_one
from test_runs_agent import create,scripted,tool_reply,final_fact


def test_general_reply_cannot_retain_revoked_prompt_metadata(employee,monkeypatch):
    scripted(monkeypatch,[{'content':json.dumps({'intent':'general','answer':'你之前看到的项目名是北辰。'})}])
    conv,id_=create(employee,'把可见项目名写成一句话');work_one(Run)
    with transaction() as db:
        db.execute(delete(Membership).where(Membership.user_id=='u1'));bump_epoch(db)
    answer=employee.get('/api/conversations/'+conv+'/messages').json()['messages'][0]['answer']
    assert '北辰' not in answer['answer']


def test_clarification_candidates_hidden_after_revocation(employee):
    with transaction() as db:
        from knowledge_agent.db import Project
        db.add(Project(id='other',code='OTHER',name='北辰',org_visible=True))
    conv,id_=create(employee,'北辰项目谁负责？');work_one(Run)
    with transaction() as db:
        db.execute(delete(Membership).where(Membership.user_id=='u1'));bump_epoch(db)
    answer=employee.get('/api/conversations/'+conv+'/messages').json()['messages'][0]['answer']
    assert not answer.get('clarification')


def test_declared_oversize_upload_rejected_before_parser(client):
    from knowledge_agent.config import settings
    response=client.post('/api/admin/documents',content=b'x',headers={'Content-Length':str(settings.max_upload_bytes*10),'Content-Type':'application/octet-stream'})
    assert response.status_code==413


def test_actual_oversize_json_rejected_without_content_length(client):
    def body():
        yield b'{' + b' '*70000 + b'}'
    assert client.post('/api/auth/register',content=body(),headers={'Content-Type':'application/json'}).status_code==413


def test_upload_rollback_does_not_leave_orphan_file():
    from knowledge_agent.documents import upload
    from knowledge_agent.config import settings
    before=set(settings.file_root.glob('*'))
    with pytest.raises(RuntimeError):
        with transaction() as db:
            upload(db,'d1','rollback','x.txt',b'synthetic')
            raise RuntimeError('force rollback')
    assert set(settings.file_root.glob('*'))==before


@pytest.mark.parametrize('reply',[None,[],{'tool_calls':[None]},{'tool_calls':[{'function':None}]}])
def test_malformed_provider_message_returns_controlled_failure(employee,monkeypatch,reply):
    scripted(monkeypatch,[reply])
    _,id_=create(employee,'P1负责人？');work_one(Run)
    result=employee.get('/api/runs/'+id_).json()
    assert result['answer'] and 'INVALID_MODEL_RESPONSE' in result['answer']['warnings']


def test_general_intent_after_forbidden_tool_is_not_success(employee,monkeypatch):
    scripted(monkeypatch,[tool_reply('get_project_owner',{'project_ref':'P2'}),
        {'content':json.dumps({'intent':'general','answer':'秘密项目负责人是某某。'})}])
    _,id_=create(employee,'P2负责人？');work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert '某某' not in answer['answer']


def test_explicit_project_cannot_use_general_intent_to_bypass_evidence(employee,monkeypatch):
    scripted(monkeypatch,[{'content':json.dumps({'intent':'general','answer':'P1负责人是捏造的人名'})}])
    _,id_=create(employee,'P1负责人？');work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert '捏造' not in answer['answer'] and 'EVIDENCE_REQUIRED' in answer['warnings']


def test_project_read_rechecks_epoch_before_return(employee,monkeypatch):
    from knowledge_agent import api
    original=api.visible_projects
    def change_during_read(db,who):
        result=original(db,who)
        bump_epoch(db)
        return result
    monkeypatch.setattr(api,'visible_projects',change_during_read)
    response=employee.get('/api/projects')
    assert response.status_code==409 and '北辰' not in response.text


def test_group_grant_and_deletion_serialize():
    from knowledge_agent.db import engine,ACL
    if engine.dialect.name!='postgresql':pytest.skip('Requires PostgreSQL row locks')
    from knowledge_agent import api
    from knowledge_agent.security import identity
    from knowledge_agent.schemas import ACLWrite
    from sqlalchemy import event
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    import threading
    read=threading.Event();release=threading.Event()
    with transaction() as db:who=identity(db,'admin')
    def pause(conn,cursor,statement,params,context,many):
        if threading.current_thread().name.startswith('grant') and 'FROM groups' in statement:
            read.set();assert release.wait(5)
    event.listen(engine,'after_cursor_execute',pause)
    try:
        with ThreadPoolExecutor(1,thread_name_prefix='grant') as writer,ThreadPoolExecutor(1,thread_name_prefix='delete') as remover:
            pending=writer.submit(api.set_acl,'project','p1',ACLWrite(org_visible=False,grants=[{'subject_type':'group','subject_id':'g1'}]),who)
            assert read.wait(5)
            deleted=remover.submit(api.delete_group,'g1',who)
            try:
                with pytest.raises(TimeoutError):deleted.result(timeout=.15)
            finally:release.set()
            pending.result(timeout=5);deleted.result(timeout=5)
        with transaction() as db:
            assert db.get(Group,'g1') is None
            assert not db.scalar(select(ACL.subject_id).where(ACL.subject_type=='group',ACL.subject_id=='g1'))
    finally:
        release.set();event.remove(engine,'after_cursor_execute',pause)

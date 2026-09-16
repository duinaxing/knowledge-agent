import time
import pytest
from sqlalchemy import select
from knowledge_agent.db import transaction, Run, Conversation, Job, Feedback, RunEvent
from knowledge_agent.security import bump_epoch, AppError
from knowledge_agent.worker import claim, purge_expired
from knowledge_agent.runs import reserve, mark_non_retryable
from knowledge_agent.agent import ask_model
from test_runs_agent import create


def test_no_retry_after_permanent_result(employee):
    _,id_=create(employee);_,gen=claim(Run)
    reserve(id_,gen,'tool','get_project_status:x')
    mark_non_retryable(id_,gen,'get_project_status:x')
    with pytest.raises(AppError) as exc:reserve(id_,gen,'tool','get_project_status:x')
    assert exc.value.code=='NON_RETRYABLE_RESULT'


def test_only_one_search_rewrite(employee):
    _,id_=create(employee);_,gen=claim(Run)
    reserve(id_,gen,'tool','search_documents:first')
    reserve(id_,gen,'tool','search_documents:rewrite')
    with pytest.raises(AppError) as exc:reserve(id_,gen,'tool','search_documents:third')
    assert exc.value.code=='QUERY_REWRITE_LIMIT'


def test_generation_retries_once_and_counts(employee,monkeypatch):
    from knowledge_agent import models
    calls=[]
    def temporary(*a,**kw):
        calls.append(1)
        if len(calls)==1:raise AppError('UNAVAILABLE')
        return {'content':'ok'},{'usage':None}
    monkeypatch.setattr(models,'chat',temporary)
    _,id_=create(employee);_,gen=claim(Run)
    assert ask_model({'run_id':id_,'generation':gen,'messages':[],'evidence':[]})['content']=='ok'
    with transaction() as db:assert db.get(Run,id_).model_count==2


def test_generation_permanent_failure_not_retried(employee,monkeypatch):
    from knowledge_agent import models
    monkeypatch.setattr(models,'chat',lambda *a,**kw: (_ for _ in ()).throw(AppError('PROVIDER_REJECTED')))
    _,id_=create(employee);_,gen=claim(Run)
    with pytest.raises(AppError):ask_model({'run_id':id_,'generation':gen,'messages':[],'evidence':[]})
    with transaction() as db:assert db.get(Run,id_).model_count==1


def test_history_retained_while_checkpoints_purged(employee):
    conv,id_=create(employee,'用户粘贴的资料')
    with transaction() as db:
        c=db.get(Conversation,conv);c.created_at=time.time()-8*86400
        r=db.get(Run,id_);r.state='completed';r.answer={'answer':'旧资料'};r.generation=2
        db.add(Feedback(run_id=id_,owner_id='u1',type='citation',note='拷贝资料'))
        bump_epoch(db)
    removed=[]
    class Saver:
        def delete_thread(self,thread_id):removed.append(thread_id)
    purge_expired(Saver())
    with transaction() as db:
        assert db.get(Run,id_).message=='用户粘贴的资料' and db.get(Run,id_).answer=={'answer':'旧资料'}
        assert db.scalar(select(Feedback)).note=='[已清理]'
    assert removed==[id_+':1',id_+':2']

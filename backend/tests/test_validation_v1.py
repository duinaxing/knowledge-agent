from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import pytest
from knowledge_loadtest.budget import reserve
from knowledge_quality.dataset import build,fingerprint,load
from knowledge_quality.review import aggregate
from knowledge_quality.runner import score
from knowledge_quality.recovery import sanitize,validate,inside
from knowledge_agent.db import engine,transaction,Run,Conversation


def test_atomic_persistent_budget(tmp_path):
    path=tmp_path/'budget.sqlite'
    with ThreadPoolExecutor(max_workers=12) as pool:
        result=list(pool.map(lambda n:reserve(path,'model',20,str(n)),range(100)))
    assert sum(result)==20
    assert not reserve(path,'model',20,'after-restart')
    assert reserve(path,'question',1,'question1')
    assert reserve(path,'question',1,'question1')
    assert not reserve(path,'question',1,'question2')


def test_dataset_frozen_balanced_and_disjoint():
    data=load();assert fingerprint(data)==fingerprint(build())
    cases=data['cases'];assert len(cases)==150
    assert sum(c['split']=='dev' for c in cases)==50
    assert len({c['id'] for c in cases})==150
    assert len({c['topic'] for c in cases if c['split']=='dev'} & {c['topic'] for c in cases if c['split']=='heldout'})==0
    for category in {c['category'] for c in cases}:assert sum(c['category']==category for c in cases)==15
    assert sum(len(c['steps']) for c in cases)==165


def test_empty_reviews_do_not_pass():
    assert aggregate([{'category':'named'}])['task_success_rate'] is None
    assert aggregate([])['verdict']=='PENDING'


def test_review_rejects_inconsistent_facts():
    with pytest.raises(ValueError):aggregate([{'reviewer':'tester','reference_approved':'1','human_task_success':'1','human_cited_facts':'1','human_supported_facts':'2'}])


def test_recall_requires_target_version_and_chunk():
    case={'targets':[{'document_id':'d','version_label':'v1','contains':'new'}],'required_facts':['new'],'forbidden':['old'],'expected_kind':'rag'}
    fixture={'documents':[{'id':'d','version_label':'v1','chunks':[{'id':'newchunk','text':'new'}]}]}
    answer={'answer':'new','evidence':[{'chunk_id':'oldchunk'}]}
    assert score(case,answer,fixture)['recall_at_6']==0


def test_restore_disables_inflight_and_keeps_completed():
    with transaction() as db:
        for i,state in enumerate(('queued','running','interrupted','completed')):
            db.add(Conversation(id=f'restore{i}',owner_id='u1',epoch=1));db.flush()
            db.add(Run(id=f'restore{i}',conversation_id=f'restore{i}',owner_id='u1',epoch=1,client_request_id='x',message='x',state=state,deadline=9999999999,generation=3))
    with engine.begin() as db:sanitize(db)
    with transaction() as db:
        for i in range(3):
            run=db.get(Run,f'restore{i}');assert run.state=='failed';assert run.generation==4
        assert db.get(Run,'restore3').state=='completed'


def test_path_escape_refused(tmp_path):
    with pytest.raises(RuntimeError):inside(tmp_path/'..'/'escape',tmp_path)


def test_missing_and_corrupt_backup(tmp_path,monkeypatch):
    import knowledge_quality.recovery as recovery
    monkeypatch.setattr(recovery,'AREA',tmp_path)
    monkeypatch.setattr(recovery,'ROOT',tmp_path)
    (tmp_path/'manifest.json').write_text(json.dumps({'schema_version':1,'source_database':'knowledge_quality_test','files':{'database.dump':'wrong'}}))
    with pytest.raises(RuntimeError):validate(tmp_path)
    (tmp_path/'database.dump').write_bytes(b'corrupt')
    with pytest.raises(RuntimeError):validate(tmp_path)

"""Validation infrastructure must refuse business data and quota resets."""
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy.engine import make_url
from knowledge_loadtest.budget import reserve
from knowledge_quality.recovery import backup,restore
from knowledge_quality.review import aggregate


def test_quality_guard_in_fresh_process():
    root=Path(__file__).resolve().parents[2]
    code='''from knowledge_loadtest.common import guard,DB_NAME,AREA
assert DB_NAME=='knowledge_quality_test'
guard('postgresql+psycopg://u:p@127.0.0.1/knowledge_quality_test',AREA/'files')
try:guard('postgresql+psycopg://u:p@127.0.0.1/knowledge',AREA/'files')
except RuntimeError:pass
else:raise AssertionError('Business database allowed')
'''
    result=subprocess.run([sys.executable,'-c',code],env={**os.environ,'VALIDATION_SUITE':'quality','PYTHONPATH':str(root/'backend/src')},capture_output=True)
    assert result.returncode==0,result.stderr.decode(errors='replace')


def test_no_business_backup():
    with pytest.raises(RuntimeError,match='non-test'):backup(make_url('postgresql+psycopg://u:p@127.0.0.1/knowledge'))


def test_no_business_drill_fixture():
    from knowledge_quality.recovery import interrupted_fixture
    with pytest.raises(RuntimeError,match='Unsafe drill source'):
        with interrupted_fixture(make_url('postgresql+psycopg://u:p@127.0.0.1/knowledge')):pass


def test_restore_fingerprint_detects_permission_and_content_changes():
    from knowledge_agent.db import engine
    from knowledge_quality.recovery import preserved_data
    from sqlalchemy import text
    with engine.connect() as db:
        baseline=preserved_data(db)
        assert baseline==preserved_data(db)
        db.execute(text("UPDATE documents SET title='tampered' WHERE id='d1'"))
        changed=preserved_data(db)
        assert changed['documents']!=baseline['documents']
        assert changed['completed_answers']==baseline['completed_answers']
        db.rollback()


def test_request_and_model_caps_are_independent(tmp_path):
    file=tmp_path/'ledger.sqlite'
    with ThreadPoolExecutor(max_workers=8) as pool:
        answers=list(pool.map(lambda i:reserve(file,'question',7,str(i)),range(25)))
    assert sum(answers)==7
    assert reserve(file,'model',1,'attempt-1')
    assert not reserve(file,'model',1,'retry-2')
    # Opening another connection/process cannot reset the same ledger.
    result=subprocess.run([sys.executable,'-c',
        'import sys;from knowledge_loadtest.budget import reserve;assert not reserve(sys.argv[1],"question",7,"restart")',str(file)],
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')},capture_output=True)
    assert result.returncode==0,result.stderr.decode(errors='replace')


def test_incomplete_fact_review_cannot_pass():
    result=aggregate([{'id':'x','reviewer':'independent','reference_approved':'1','human_task_success':'1','category':'named'}])
    assert result['verdict']=='PENDING'


def test_review_reports_failure_without_hiding_incorrect_answers():
    rows=[{'id':'r','reviewer':'independent','reference_approved':'1','human_task_success':'0','category':'named','human_supported_facts':'0','human_cited_facts':'2'},
          {'id':'n','reviewer':'independent','reference_approved':'1','human_task_success':'1','category':'insufficient','human_refusal_correct':'1'}]
    result=aggregate(rows)
    assert result['verdict']=='FAIL' and result['task_success_rate']==.5 and result['citation_support_rate']==0

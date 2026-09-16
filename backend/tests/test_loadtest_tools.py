import io
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from pypdf import PdfReader
from knowledge_loadtest.common import guard, AREA, pdf_bytes
from knowledge_loadtest.gateway import reserve, mock_message
from knowledge_loadtest.report import percentile


@pytest.mark.parametrize('db',['knowledge','knowledge_test_project2_20260912','knowledge_loadtest_100_typo'])
def test_loadtest_refuses_other_database(db):
    with pytest.raises(RuntimeError):guard(f'postgresql+psycopg://x:x@127.0.0.1/{db}',AREA/'files')


def test_loadtest_refuses_other_file_root(tmp_path):
    with pytest.raises(RuntimeError):guard('postgresql+psycopg://x:x@127.0.0.1/knowledge_loadtest_100',tmp_path)


def test_gateway_budget_is_atomic(tmp_path):
    with ThreadPoolExecutor(max_workers=16) as executor:
        result=list(executor.map(lambda _:reserve(tmp_path/'budget.sqlite',25),range(80)))
    assert sum(result)==25


def test_pdf_fixture_extractable():
    reader=PdfReader(io.BytesIO(pdf_bytes(['TESTDOC-001','Verification code: VERIFY-12345678.'])))
    assert len(reader.pages)==1
    assert 'VERIFY-12345678' in reader.pages[0].extract_text()


def test_mock_uses_actual_tool_evidence():
    messages=[{'role':'user','content':json.dumps({'question':'Code in TESTDOC-001?'})}]
    assert mock_message(messages)['tool_calls'][0]['function']['name']=='search_documents'
    messages.append({'role':'user','content':'证据：'+json.dumps([{'id':'E2','text':'TESTDOC-001 Verification code: VERIFY-12345678.'}])})
    answer=json.loads(mock_message(messages)['content'])
    assert answer['facts'][0]['evidence_ids']==['E2']
    assert 'VERIFY-12345678' in answer['facts'][0]['text']


def test_percentile_includes_timeouts():
    assert percentile([1]*94+[60]*6,.95)==60


def test_worker_attaches_run_id_only_to_test_gateway(monkeypatch):
    from knowledge_loadtest import worker as instrument
    from knowledge_agent import worker,agent,models
    captured=[]
    def original_post(base,path,key,payload,timeout):captured.append(payload);return {}
    def ask(state):
        models._post('http://127.0.0.1:8102','/chat/completions','test',{'messages':[]},20)
        models._post('http://127.0.0.1:8101','/embeddings','test',{'input':[]},5)
    monkeypatch.setattr(instrument,'guard',lambda:None)
    monkeypatch.setattr(agent,'ask_model',ask)
    monkeypatch.setattr(models,'_post',original_post)
    monkeypatch.setattr(worker,'claim',lambda cls:None)
    monkeypatch.setattr(worker,'main',lambda:agent.ask_model({'run_id':'load-run'}))
    instrument.main()
    assert captured[0]['_load_run_id']=='load-run'
    assert '_load_run_id' not in captured[1]

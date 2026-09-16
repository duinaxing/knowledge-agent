from types import SimpleNamespace
import pytest
from knowledge_agent.agent import simple_document_query
from knowledge_agent.db import Run
from knowledge_agent.worker import work_one
from test_runs_agent import create,scripted,final_fact,tool_reply


@pytest.mark.parametrize('question', ['比较公开规范与秘密规范','公开规范历史规定是什么','公开规范负责人是谁','Compare 公开规范 and another file','请分别总结公开规范和其他资料'])
def test_complex_questions_keep_agent(question):
    assert simple_document_query(question,[SimpleNamespace(title='公开规范')]) is None


def test_named_authorized_document_uses_one_model_call(employee,monkeypatch):
    scripted(monkeypatch,[final_fact('验收标准为 P1 缺陷为零。')])
    _,id_=create(employee,'公开规范的验收标准是什么？')
    work_one(Run)
    r=employee.get('/api/runs/'+id_).json()
    assert r['answer']['status']=='answered'
    assert r['model_count']==1 and r['tool_count']==1
    assert r['answer']['evidence'][0]['document_id']=='d1'


def test_unlisted_document_does_not_trigger_fast_path():
    assert simple_document_query('秘密规范有什么要求',[SimpleNamespace(title='公开规范')]) is None


def test_empty_fast_search_returns_to_agent(employee,monkeypatch):
    from knowledge_agent import agent
    original=agent.search
    count=0
    def search(*args,**kwargs):
        nonlocal count
        count+=1
        return ([],[]) if count==1 else original(*args,**kwargs)
    monkeypatch.setattr(agent,'search',search)
    scripted(monkeypatch,[tool_reply('search_documents',{'query':'验收'}),{'content':''},final_fact('验收标准为 P1 缺陷为零。')])
    _,id_=create(employee,'公开规范的验收标准是什么？');work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()
    assert answer['answer']['status']=='answered'
    assert count==2 and answer['model_count']==3


def test_fast_answer_still_rejects_fabricated_citation(employee,monkeypatch):
    scripted(monkeypatch,[final_fact('不应接受的结论','E999')])
    _,id_=create(employee,'公开规范的验收标准是什么？');work_one(Run)
    answer=employee.get('/api/runs/'+id_).json()['answer']
    assert answer['status']=='partial' and answer['facts']==[]
    assert '不应接受的结论' not in answer['answer']

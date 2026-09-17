import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
import httpx
from fastapi import FastAPI, Request, HTTPException
from .common import AREA, guard, SUITE
from .budget import reserve as admission

app=FastAPI(title='Isolated load-test model gateway')


def reserve(path, cap):
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, used INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO budget VALUES (1,0)')
        changed=db.execute('UPDATE budget SET used=used+1 WHERE id=1 AND used<?',(cap,)).rowcount
        return bool(changed)


def mock_message(messages):
    context=json.loads(next(m['content'] for m in messages if m['role']=='user'))
    question=context['question']
    if 'previous specification' in question:
        history=context.get('previous_turns_for_reference_only',[])
        if history:question=history[-1]['question']
    unnamed=re.search(r'specification number (\d{3})',question)
    if unnamed:question+=' TESTDOC-'+unnamed[1]
    match=re.search(r'TESTDOC-\d{3}',question)
    if not match:
        return {'content':json.dumps({'intent':'general','answer':'A good meeting has an agenda, clear decisions and action owners.'})}
    last=messages[-1]
    if last['role']=='user' and '证据：' in last['content']:
        evidence=json.loads(last['content'].split('证据：',1)[1])
        facts=[]
        for e in evidence:
            if match[0] in e.get('text',''):
                code=re.search(r'VERIFY-\d+',e['text'])
                if code:
                    facts=[{'text':f'The verification code is {code[0]}.','evidence_ids':[e['id']]}];break
        if question.startswith('Compare '):
            facts=[]
            for title in dict.fromkeys(re.findall(r'TESTDOC-\d{3}',question)):
                for e in evidence:
                    code=re.search(r'VERIFY-\d+',e.get('text',''))
                    if title in e.get('text','') and code:
                        facts.append({'text':f'{title}: {code[0]}.','evidence_ids':[e['id']]});break
        return {'content':json.dumps({'status':'answered' if facts else 'no_answer','facts':facts,'warnings':[]})}
    if any(m['role']=='tool' for m in messages):
        titles=list(dict.fromkeys(re.findall(r'TESTDOC-\d{3}',question)))
        if question.startswith('Compare ') and len(titles)>1 and sum(m['role']=='tool' for m in messages)==1:
            return {'content':None,'tool_calls':[{'id':'second_search','type':'function','function':{'name':'search_documents','arguments':json.dumps({'query':titles[1]+' verification code','mode':'current'})}}]}
        return {'content':'{}'}
    return {'content':None,'tool_calls':[{'id':'load_search','type':'function','function':{'name':'search_documents','arguments':json.dumps({'query':question,'mode':'current'})}}]}


def quality_mock(messages):
    """Protocol fixture only; never treated as a semantic judge or quality score."""
    question=json.loads(next(m['content'] for m in messages if m['role']=='user'))['question']
    if '二分查找' in question:return {'content':json.dumps({'intent':'general','answer':'二分查找在有序范围中逐步折半定位目标。'})}
    if '证据：' in messages[-1].get('content',''):
        evidence=json.loads(messages[-1]['content'].split('证据：',1)[1]);facts=[]
        if '成本' not in question and '预算' not in question:
            for e in evidence:
                codes=re.findall(r'(?:CHECK|OLD)-\d+|支持期为 \d+ 天',e.get('text',''))
                if codes:facts.append({'text':'；'.join(codes),'evidence_ids':[e['id']]})
        return {'content':json.dumps({'status':'answered' if facts else 'no_answer','facts':facts,'warnings':[]})}
    if any(m['role']=='tool' for m in messages):return {'content':'{}'}
    return {'content':None,'tool_calls':[{'id':'quality_search','type':'function','function':{'name':'search_documents',
             'arguments':json.dumps({'query':question,'mode':'history' if '过去' in question else 'current'})}}]}


@app.get('/health')
def health(): return {'ready':True}


@app.post('/chat/completions')
async def chat(request:Request):
    guard()
    if request.headers.get('authorization')!='Bearer '+os.environ['LOAD_GATEWAY_TOKEN']: raise HTTPException(403)
    run=Path(os.environ['LOAD_RESULT_DIR']);run.mkdir(parents=True,exist_ok=True)
    payload=await request.json();mode=os.environ.get('LOAD_MODEL_MODE','mock')
    run_id=payload.pop('_load_run_id',None)
    start=time.monotonic();status=200;usage=None;budget=False
    try:
        if mode=='real':
            ledger=Path(os.environ.get('VALIDATION_BUDGET_FILE',str(run/'admissions.sqlite')))
            if not admission(ledger,'model',1500 if SUITE=='quality' else 1000,uuid.uuid4().hex):
                budget=True;raise HTTPException(402,'LOAD_BUDGET_EXHAUSTED')
            async with httpx.AsyncClient(timeout=22) as client:
                response=await client.post(os.environ['LOAD_UPSTREAM_URL'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+os.environ['LOAD_UPSTREAM_KEY']},json=payload)
                status=response.status_code
                if status!=200: raise HTTPException(status,'UPSTREAM_ERROR')
                result=response.json();usage=result.get('usage');return result
        fault_file=run/'fault.json'
        fault=json.loads(fault_file.read_text()) if fault_file.exists() else {}
        if fault.get('until',0)<time.time(): fault={}
        kind=fault.get('kind')
        if kind=='429': raise HTTPException(429,'INJECTED_RATE_LIMIT')
        await asyncio.sleep(25 if kind=='timeout' else 8 if kind in ('delay','worker') else 2)
        return {'model':'LOAD_MOCK','choices':[{'message':(quality_mock if SUITE=='quality' else mock_message)(payload['messages'])}],'usage':None}
    except HTTPException as exc:
        status=exc.status_code;raise
    except (httpx.HTTPError, ValueError,KeyError):
        status=502;raise HTTPException(502,'GATEWAY_FAILURE') from None
    finally:
        row={'at':time.time(),'run_id':run_id,'mode':mode,'status':status,'seconds':time.monotonic()-start,'usage':usage,'budget_exhausted':budget}
        with (run/'gateway.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')

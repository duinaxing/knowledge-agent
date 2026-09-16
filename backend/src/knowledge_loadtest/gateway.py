import asyncio
import json
import os
import re
import sqlite3
import time
from pathlib import Path
import httpx
from fastapi import FastAPI, Request, HTTPException
from .common import AREA, guard

app=FastAPI(title='Isolated load-test model gateway')


def reserve(path, cap):
    with sqlite3.connect(path, timeout=10) as db:
        db.execute('CREATE TABLE IF NOT EXISTS budget (id INTEGER PRIMARY KEY, used INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO budget VALUES (1,0)')
        changed=db.execute('UPDATE budget SET used=used+1 WHERE id=1 AND used<?',(cap,)).rowcount
        return bool(changed)


def mock_message(messages):
    question=json.loads(next(m['content'] for m in messages if m['role']=='user'))['question']
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
        return {'content':json.dumps({'status':'answered' if facts else 'no_answer','facts':facts,'warnings':[]})}
    if any(m['role']=='tool' for m in messages): return {'content':'{}'}
    return {'content':None,'tool_calls':[{'id':'load_search','type':'function','function':{'name':'search_documents','arguments':json.dumps({'query':question,'mode':'current'})}}]}


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
            if not reserve(run/'budget.sqlite',1000):
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
        await asyncio.sleep(25 if kind=='timeout' else 8 if kind=='delay' else 2)
        return {'model':'LOAD_MOCK','choices':[{'message':mock_message(payload['messages'])}],'usage':None}
    except HTTPException as exc:
        status=exc.status_code;raise
    except (httpx.HTTPError, ValueError,KeyError):
        status=502;raise HTTPException(502,'GATEWAY_FAILURE') from None
    finally:
        row={'at':time.time(),'run_id':run_id,'mode':mode,'status':status,'seconds':time.monotonic()-start,'usage':usage,'budget_exhausted':budget}
        with (run/'gateway.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row)+'\n')

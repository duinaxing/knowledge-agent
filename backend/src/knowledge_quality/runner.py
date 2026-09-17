import asyncio
import csv
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
import httpx
from knowledge_loadtest.common import AREA,ROOT,guard,write_json
from knowledge_loadtest.budget import reserve
from .dataset import load,fingerprint


def score(case,answer,fixture):
    evidence=answer.get('evidence',[])
    found={e.get('chunk_id') for e in evidence[:6]}
    hits=[]
    for target in case['targets']:
        chunks={c['id'] for d in fixture['documents'] if d['id']==target['document_id'] and d['version_label']==target['version_label']
                for c in d['chunks'] if target['contains'] in c['text']}
        hits.append(bool(chunks & found))
    text=answer.get('answer','')
    return {'recall_at_6':sum(hits)/len(hits) if hits else None,
            'fact_tokens_present':all(f in text for f in case['required_facts']),
            'forbidden_present':any(f in text for f in case['forbidden']),
            'routing_match':answer.get('kind')=='general' if case['expected_kind']=='general' else answer.get('kind')!='general',
            'human_task_success':None,'human_supported_facts':None,'human_cited_facts':None,
            'human_refusal_correct':None,'reference_review':'pending'}


def report(out,rows,real):
    measured=[r['recall_at_6'] for r in rows if r.get('recall_at_6') is not None]
    summary={'mode':'real' if real else 'mock_protocol_only','cases':len(rows),
             'recall_at_6':sum(measured)/len(measured) if measured else None,
             'failures':sum(bool(r.get('error')) for r in rows),'review_coverage':0,
             'human_task_success_rate':None,'citation_support_rate':None,'verdict':'PENDING_REVIEW' if real else 'NOT_SEMANTIC_EVALUATION'}
    summary['categories']={}
    outcome=json.loads((out/'outcome.json').read_text()) if (out/'outcome.json').exists() else {}
    summary['halted']=outcome.get('halted');summary['planned_cases']=outcome.get('planned')
    if summary['halted']:
        summary['verdict']='BUDGET_LIMITED' if 'BUDGET' in summary['halted'] else 'STOPPED'
    for category in sorted({r['category'] for r in rows}):
        group=[r for r in rows if r['category']==category];recall=[r['recall_at_6'] for r in group if r.get('recall_at_6') is not None]
        summary['categories'][category]={'cases':len(group),'errors':sum(bool(r.get('error')) for r in group),
            'recall_at_6':sum(recall)/len(recall) if recall else None,
            'fact_token_checks_passed':sum(r.get('fact_tokens_present',False) for r in group),
            'routing_matches':sum(r.get('routing_match',False) for r in group)}
    write_json(out/'summary.json',summary)
    with (out/'review.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        fields=['id','split','category','question','required_facts','answer','human_task_success','human_supported_facts','human_cited_facts','human_refusal_correct','reference_approved','reviewer','notes']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for row in rows:
            writer.writerow({**{key:row.get(key,'') for key in fields},'answer':json.dumps(row.get('answer'),ensure_ascii=False)})
    (out/'REPORT.md').write_text('# Quality evaluation\n\n'+json.dumps(summary,ensure_ascii=False,indent=2)+
        '\n\nMock output only checks the protocol and validators. Semantic acceptance requires real results and independent review. Failed cases remain in results.jsonl.\n',encoding='utf-8')


async def run(out,real):
    guard();data=load();fixture=json.loads((AREA/'fixture.json').read_text(encoding='utf-8'))
    if fixture['dataset_sha256']!=fingerprint(data):raise RuntimeError('Fixture mismatch')
    password=json.loads((AREA/'credentials.json').read_text())['password']
    split=os.environ.get('QUALITY_SPLIT','dev')
    if split not in ('dev','heldout'):raise RuntimeError('Invalid split')
    cases=[c for c in data['cases'] if c['split']==split]
    write_json(out/'manifest.json',{'dataset_sha256':fingerprint(data),'split':split,'schema_version':1,
               'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
               'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)),
               'model':os.environ.get('MODEL_NAME','configured'),'embedding':os.environ['EMBEDDING_MODEL'],'chunk_chars':600,'overlap_chars':100,
               'corpus_sha256':fingerprint(data['documents'])})
    rows=[];last_submit=0;halted=None
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8100',headers={'Origin':'http://127.0.0.1:5173'},timeout=15) as client:
        async def login():
            response=await client.post('/api/auth/login',json={'username':'quality0','password':password});response.raise_for_status()
            client.headers['x-csrf-token']=response.json()['csrf']
        await login()
        # Second identity must not read this user's sessions; hidden document must stay hidden.
        own=await client.post('/api/conversations');own.raise_for_status()
        async with httpx.AsyncClient(base_url=str(client.base_url),headers={'Origin':'http://127.0.0.1:5173'},timeout=15) as other:
            auth=await other.post('/api/auth/login',json={'username':'quality1','password':password});auth.raise_for_status()
            probe=await other.get(f"/api/conversations/{own.json()['id']}/messages")
            if probe.status_code!=404:raise RuntimeError('CONVERSATION_ISOLATION_FAILED')
        hidden=next(d for d in fixture['documents'] if not d['public'])
        probe=await client.get(f"/api/documents/{hidden['id']}/versions/{hidden['version_id']}/chunks/{hidden['chunks'][0]['id']}")
        if probe.status_code!=404:raise RuntimeError('DOCUMENT_ISOLATION_FAILED')
        try:
            for case in cases:
                row={'id':case['id'],'split':split,'category':case['category'],'question':case['steps'],'required_facts':case['required_facts'],
                     'reference_targets':case['targets'],'expected_kind':case['expected_kind'],'forbidden':case['forbidden'],
                     'authorized_documents':case['authorized_documents'],'turns':[]}
                started=time.monotonic()
                try:
                    response=await client.post('/api/conversations');response.raise_for_status();conv=response.json()['id']
                    for message in case['steps']:
                        await asyncio.sleep(max(0,3.1-(time.monotonic()-last_submit)))
                        request_id=uuid.uuid4().hex
                        if real and not reserve(Path(os.environ['VALIDATION_BUDGET_FILE']),'question',300,request_id):
                            halted='QUESTION_BUDGET_EXHAUSTED';raise RuntimeError(halted)
                        last_submit=time.monotonic()
                        response=await client.post(f'/api/conversations/{conv}/queries',json={'message':message,'client_request_id':request_id})
                        response.raise_for_status();run_id=response.json()['run_id']
                        # Events carry task state, not tokens. Poll once after stream completion.
                        try:
                            async with asyncio.timeout(75):
                                async with client.stream('GET',f'/api/runs/{run_id}/events',timeout=70) as stream:
                                    stream.raise_for_status()
                                    async for line in stream.aiter_lines():
                                        if line.startswith('event:') and line.split(':',1)[1].strip() in ('done','error'):break
                        except (httpx.TimeoutException,TimeoutError):pass
                        response=await client.get(f'/api/runs/{run_id}');response.raise_for_status();result=response.json()
                        row['turns'].append(result)
                        if result['state']!='completed':raise RuntimeError(result.get('error_code') or result['state'])
                        answer=result.get('answer') or {}
                        for e in answer.get('evidence',[]):
                            if e.get('type')!='document':continue
                            versions=[d for d in fixture['documents'] if d['id']==e.get('document_id') and d['version_id']==e.get('version_id') and d['public']]
                            chunks=[c for d in versions for c in d['chunks'] if c['id']==e.get('chunk_id')]
                            if not chunks or chunks[0]['text']!=e.get('text'):
                                halted='INVALID_OR_UNAUTHORIZED_CITATION';raise RuntimeError(halted)
                            rr=await client.get(f"/api/documents/{e['document_id']}/versions/{e['version_id']}/chunks/{e['chunk_id']}")
                            if rr.status_code!=200:halted='INACCESSIBLE_CITATION';raise RuntimeError(halted)
                        history=await client.get(f'/api/conversations/{conv}/messages');history.raise_for_status()
                        if not any(m['id']==run_id and m.get('answer')==answer for m in history.json()['messages']):
                            halted='HISTORY_MISMATCH';raise RuntimeError(halted)
                    row.update(answer=answer,**score(case,answer,fixture))
                    if row['forbidden_present'] and case['category'] in ('permission','injection'):
                        halted='SECURITY_CASE_FAILED'
                    row['conversation_id']=conv
                except (httpx.HTTPError,RuntimeError,KeyError) as exc:
                    row['error']=str(exc) if isinstance(exc,RuntimeError) else type(exc).__name__
                    row['recall_at_6']=0 if case['targets'] else None
                row['seconds']=time.monotonic()-started;rows.append(row)
                with (out/'results.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
                print('Quality:',case['id'],row.get('error','recorded'),flush=True)
                # A rejected gateway call is a budget limit, not a provider outage.
                gateway=out/'gateway.jsonl'
                if gateway.exists() and any(json.loads(line).get('budget_exhausted') for line in gateway.read_text().splitlines()):halted='MODEL_BUDGET_EXHAUSTED'
                if halted:break
            await login()
            for row in rows:
                if not row.get('conversation_id'):continue
                r=await client.get(f"/api/conversations/{row['conversation_id']}/messages");r.raise_for_status()
                if not any(m['id']==row['turns'][-1]['id'] for m in r.json()['messages']):halted='RELOGIN_HISTORY_LOST'
        finally:
            write_json(out/'outcome.json',{'halted':halted,'cases':len(rows),'planned':len(cases)})
            report(out,rows,real)

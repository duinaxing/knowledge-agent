import argparse
import asyncio
from collections import deque
import json
import os
from pathlib import Path
import random
import time
import uuid
import httpx
from .common import AREA, guard, write_json
from .monitor import monitor, database_sample
from .budget import reserve as admission


class Harness:
    def __init__(self, out, profile, restart_worker=None):
        guard();self.out=Path(out);self.profile=profile;self.restart_worker=restart_worker
        self.fixture=json.loads((AREA/'fixture.json').read_text(encoding='utf-8'))
        self.password=json.loads((AREA/'credentials.json').read_text())['password']
        self.clients=[];self.phase='setup';self.halted=None;self.recent=deque();self.bad_since=None
        self.submitted=0;self.stop_monitor=asyncio.Event();self.rng=random.Random(20260915)
        self.real=os.environ.get('LOAD_MODEL_MODE')=='real';self.expected_errors=False
        self.semaphores=[asyncio.Lock() for _ in range(100)]
        self.persisted=[]

    def log(self, file, row):
        with (self.out/file).open('a',encoding='utf-8') as f:f.write(json.dumps({'phase':self.phase,'at':time.time(),**row},ensure_ascii=False)+'\n')

    def halt(self, reason):
        if not self.halted:
            self.halted=reason;self.log('stops.jsonl',{'reason':reason});print('STOP:',reason,flush=True)

    async def request(self,u,method,path,**kwargs):
        started=time.monotonic();status=0;response=None
        try:
            response=await self.clients[u].request(method,path,**kwargs);status=response.status_code
        except httpx.HTTPError: pass
        elapsed=time.monotonic()-started
        self.log('http.jsonl',{'user':u,'method':method,'endpoint':path.split('?')[0],'seconds':elapsed,'status':status,'expected_error':self.expected_errors})
        if not self.expected_errors and self.phase!='setup':
            now=time.monotonic();self.recent.append((now,status==0 or status>=400))
            while self.recent and self.recent[0][0]<now-30:self.recent.popleft()
            bad=sum(x[1] for x in self.recent)/len(self.recent)>.2
            if bad:
                self.bad_since=self.bad_since or now
                if now-self.bad_since>=30:self.halt('HTTP_ERROR_RATE')
            else:self.bad_since=None
        return response

    async def login(self,u):
        r=await self.request(u,'POST','/api/auth/login',json={'username':f'load{u:03}','password':self.password})
        if r is not None and r.status_code==429:
            await asyncio.sleep(61)
            r=await self.request(u,'POST','/api/auth/login',json={'username':f'load{u:03}','password':self.password})
        if r is None or r.status_code!=200:raise RuntimeError(f'Login failed for virtual user {u}')
        self.clients[u].headers['x-csrf-token']=r.json()['csrf']

    async def setup(self):
        for _ in range(100):
            self.clients.append(httpx.AsyncClient(base_url='http://127.0.0.1:8100',headers={'Origin':'http://127.0.0.1:5173'},timeout=15,limits=httpx.Limits(max_connections=4,max_keepalive_connections=4)))
        # Sliding-window rate <=25/min even across minute boundaries.
        for u in range(4 if self.profile=='faults' else 100):
            if u:await asyncio.sleep(2.5)
            await self.login(u)
            if u%25==24:print(f'Logged in {u+1}/100',flush=True)

    def doc(self,u,kind=0):
        eligible=[d for d in self.fixture['documents'] if u in d['allowed'] and (int(d['id'][2:])//100)==kind]
        return eligible[u%len(eligible)]

    async def excerpt(self,u,d):
        path=f"/api/documents/{d['id']}/versions/{d['version_id']}/chunks/{d['chunk_id']}"
        return await self.request(u,'GET',path)

    async def query(self,u,rag=True,planned=None):
        async with self.semaphores[u]:
            if self.halted or (self.real and self.submitted>=200):return
            r=await self.request(u,'POST','/api/conversations')
            if r is None or r.status_code not in (200,201):return
            conv=r.json()['id']
            await self.query_in(u,conv,rag,planned)

    async def query_in(self,u,conv,rag=True,planned=None,gate=None,message_override=None,extra_doc=None):
        if gate:await gate.wait()
        if self.halted or (self.real and self.submitted>=200):return
        self.submitted+=1
        if self.real and not admission(Path(os.environ.get('VALIDATION_BUDGET_FILE',str(self.out/'admissions.sqlite'))),'question',200,uuid.uuid4().hex):
            self.halt('QUESTION_BUDGET_EXHAUSTED');return
        d=self.doc(u,u%3)
        message=f"What is the verification code in {d['title']}?" if rag else 'Explain how to organize an effective meeting in one sentence.'
        if message_override is not None:message=message_override
        start=time.monotonic();scheduled=planned[0] if planned else start
        row={'user':u,'rag':rag,'scheduled':scheduled,'sent':start,'send_lag':start-scheduled,'submitted':False,'completed':False,'valid':False,'queue_seconds':None,'execution_seconds':None,'sse_reconnects':0}
        body={'message':message,'client_request_id':uuid.uuid4().hex}
        r=await self.request(u,'POST',f'/api/conversations/{conv}/queries',json=body)
        if r is None or r.status_code!=202:
            row.update(error='SUBMIT_FAILED',seconds=time.monotonic()-scheduled,finished=time.monotonic());self.log('queries.jsonl',row);return
        row['submitted']=True;run=r.json()['run_id'];row['run_id']=run
        if self.phase=='functional':
            again=await self.request(u,'POST',f'/api/conversations/{conv}/queries',json=body)
            if again is None or again.status_code!=202 or again.json().get('run_id')!=run:self.halt('IDEMPOTENCY_FAILED')
        last_id='0';claim=None;terminal=False
        # Reconnect uses Last-Event-ID. All timeouts include submission/queue time.
        while time.monotonic()-start<75 and not terminal:
            try:
                async with asyncio.timeout(max(.1,75-(time.monotonic()-start))):
                    async with self.clients[u].stream('GET',f'/api/runs/{run}/events',headers={'Last-Event-ID':last_id},timeout=10) as stream:
                        if stream.status_code!=200:break
                        async for line in stream.aiter_lines():
                            if line.startswith('id:'):last_id=line.split(':',1)[1].strip()
                            if line.startswith('event:'):
                                event=line.split(':',1)[1].strip()
                                if event in ('started','stage_started') and claim is None:claim=time.monotonic()
                                if event in ('done','error'):terminal=True;break
                if terminal and self.phase=='fault_worker' and not row.get('resumed'):
                    state=await self.request(u,'GET',f'/api/runs/{run}')
                    if state is not None and state.status_code==200 and state.json()['state']=='interrupted':
                        resumed=await self.request(u,'POST',f'/api/runs/{run}/resume')
                        row['resumed']=resumed is not None and resumed.status_code==202
                        if row['resumed']:terminal=False
                if not terminal:row['sse_reconnects']+=1;await asyncio.sleep(.25)
            except (httpx.HTTPError,TimeoutError):
                row['sse_reconnects']+=1;await asyncio.sleep(.25)
        result=await self.request(u,'GET',f'/api/runs/{run}')
        answer_finished=time.monotonic()
        if result is not None and result.status_code==200:
            data=result.json();answer=data.get('answer') or {};row['state']=data['state'];row['answer_status']=answer.get('status');row['error']=data.get('error_code')
            row['completed']=data['state']=='completed'
            row['valid']=row['completed'] and answer.get('status')=='answered' and (d['code'] in answer.get('answer','') if rag else answer.get('kind')=='general')
            if extra_doc and extra_doc['code'] not in answer.get('answer',''):row['valid']=False
            row['warnings']=answer.get('warnings',[])
            for e in answer.get('evidence',[]):
                if e.get('type')!='document':continue
                expected=next((x for x in self.fixture['documents'] if x['id']==e['document_id']),None)
                if expected is None or u not in expected['allowed']:self.halt('UNAUTHORIZED_CITATION');row['valid']=False;break
                rr=await self.excerpt(u,{'id':e['document_id'],'version_id':e['version_id'],'chunk_id':e['chunk_id']})
                if rr is None or rr.status_code!=200:self.halt('INVALID_CITATION');row['valid']=False;break
            if rag and row['valid'] and not any(e.get('chunk_id') in d['target_chunks'] for e in answer.get('evidence',[])):row['valid']=False
            if row['completed']:
                history=await self.request(u,'GET',f'/api/conversations/{conv}/messages')
                if history is None or history.status_code!=200 or not any(m['id']==run for m in history.json()['messages']):self.halt('HISTORY_LOST')
                elif u==0:self.persisted.append((conv,run,answer.get('answer')))
        row['seconds']=answer_finished-scheduled
        row['finished']=answer_finished
        row['validation_seconds']=time.monotonic()-answer_finished
        row['queue_seconds']=claim-start if claim else None
        row['execution_seconds']=time.monotonic()-claim if claim else None
        self.log('queries.jsonl',row)

    async def reads(self,u,choice):
        if choice<.4:await self.request(u,'GET','/api/conversations')
        elif choice<.7:await self.request(u,'GET',f"/api/conversations/{self.fixture['users'][u]['conversations'][0]}/messages")
        else:await self.excerpt(u,self.doc(u))

    async def mixed(self,n,seconds,read_only=False):
        finish=time.monotonic()+seconds
        async def person(u):
            rng=random.Random(1000+u)
            while time.monotonic()<finish and not self.halted:
                choice=rng.random()
                if not read_only and choice>=.8:await self.query(u,rng.random()<.7)
                else:await self.reads(u,choice)
                await asyncio.sleep(min(rng.uniform(10,30),max(0,finish-time.monotonic())))
        await asyncio.gather(*(person(u) for u in range(n)))

    async def burst(self):
        convs=[]
        for u in range(100):
            r=await self.request(u,'POST','/api/conversations')
            if r is None or r.status_code not in (200,201):raise RuntimeError('Burst conversation preparation failed')
            convs.append(r.json()['id'])
        gate=asyncio.Event();planned=[0.]
        tasks=[asyncio.create_task(self.query_in(u,convs[u],u%10<7,planned,gate)) for u in range(100)]
        await asyncio.sleep(.1);planned[0]=time.monotonic();gate.set();await asyncio.gather(*tasks)

    async def complex_questions(self,kind):
        async def person(u):
            response=await self.request(u,'POST','/api/conversations')
            if response is None or response.status_code!=201:self.halt('COMPLEX_SETUP_FAILED');return
            conv=response.json()['id'];doc=self.doc(u,u%3);extra=None
            if kind=='followup':
                await self.query_in(u,conv,True)
                message='What is the verification code of the previous specification again?'
            elif kind=='comparison':
                extra=self.fixture['documents'][(u+1)%100]
                message=f"Compare {doc['title']} and {extra['title']}; give both verification codes."
            else:message=f"What is the verification code for operational specification number {int(doc['id'][2:]):03}?"
            await self.query_in(u,conv,True,message_override=message,extra_doc=extra)
        # Fixed ten active users; explicitly not a 100-user capacity claim.
        await asyncio.gather(*(person(u) for u in range(10)))

    async def isolation(self):
        self.expected_errors=True
        try:
            for u in range(100):
                other=self.fixture['users'][(u+1)%100]['conversations'][0]
                r=await self.request(u,'GET',f'/api/conversations/{other}/messages')
                if r is None or r.status_code!=404:self.halt('CONVERSATION_ISOLATION_FAILED');return
                d=self.doc((u+1)%100,2);r=await self.excerpt(u,d)
                if r is None or r.status_code!=404:self.halt('DOCUMENT_ISOLATION_FAILED');return
        finally:self.expected_errors=False

    async def drain(self):
        start=time.monotonic()
        while time.monotonic()-start<120:
            data=await asyncio.to_thread(database_sample)
            if not any(data['states'].get(s,0) for s in ('queued','running','interrupted')):break
            await asyncio.sleep(2)
        else:self.halt('QUEUE_NOT_RECOVERED')
        samples=[]
        for _ in range(20):
            begin=time.monotonic();r=await self.request(0,'GET','/api/conversations');samples.append(time.monotonic()-begin if r is not None and r.status_code==200 else 999)
        self.log('recovery.jsonl',{'seconds':time.monotonic()-start,'read_p95':sorted(samples)[18],'queue_clear':not self.halted})

    async def phase_run(self,name,fn):
        if self.halted:return
        self.phase=name;self.recent.clear();self.bad_since=None
        print('Phase:',name,flush=True);start=time.time()
        await fn()
        self.log('phases.jsonl',{'start':start,'end':time.time()})
        await self.drain()

    async def login_peak(self):
        self.expected_errors=True
        try:
            await asyncio.gather(*(self.request(u,'POST','/api/auth/login',json={'username':f'load{u:03}','password':self.password}) for u in range(100)))
        finally:self.expected_errors=False
        # This is last: successful logins rotate client cookies; no later writes.

    async def fault(self,kind):
        self.expected_errors=True
        try:
            write_json(self.out/'fault.json',{'kind':kind,'until':time.time()+35})
            tasks=[asyncio.create_task(self.query(u,True)) for u in range(4)]
            if kind=='worker':
                for _ in range(100):
                    snapshot=await asyncio.to_thread(database_sample)
                    if snapshot['states'].get('running',0):break
                    await asyncio.sleep(.1)
                else:raise RuntimeError('Worker fault invalid: no running task observed')
                self.log('fault-injection.jsonl',{'running_before_restart':snapshot['states']['running']})
                if self.restart_worker:await asyncio.to_thread(self.restart_worker)
                else:raise RuntimeError('Worker restart callback missing')
            await asyncio.gather(*tasks)
        finally:
            write_json(self.out/'fault.json',{});self.expected_errors=False

    async def run(self):
        monitoring=asyncio.create_task(monitor(self.out,self.stop_monitor,self.halt))
        try:
            await self.setup()
            await self.phase_run('functional',lambda:self.query(0,True))
            queries=[json.loads(s) for s in (self.out/'queries.jsonl').read_text().splitlines()]
            if not queries[-1]['valid']:self.halt('FUNCTIONAL_CHECK_FAILED')
            if not self.halted and self.profile!='faults':await self.isolation()
            if self.real:
                # Functional query consumes one of the ten warmup questions.
                for i in range(9):await self.phase_run('real_warmup',lambda i=i:self.query(i,True))
                async def daily():
                    finished=asyncio.Event()
                    async def reader(u):
                        rng=random.Random(u)
                        while not finished.is_set() and not self.halted:
                            await self.reads(u,rng.random()*.8)
                            try:await asyncio.wait_for(finished.wait(),rng.uniform(10,30))
                            except TimeoutError:pass
                    readers=[asyncio.create_task(reader(u)) for u in range(100)]
                    pending=[]
                    try:
                        for i in range(90):
                            if self.halted:break
                            pending.append(asyncio.create_task(self.query(i,i%10<7)))
                            await asyncio.sleep(10)
                        await asyncio.gather(*pending)
                    finally:
                        finished.set();await asyncio.gather(*readers)
                await self.phase_run('real_mixed',daily)
                await self.phase_run('real_burst',self.burst)
            elif self.profile=='faults':
                for kind in ('delay','429','timeout','worker'):await self.phase_run('fault_'+kind,lambda kind=kind:self.fault(kind))
            elif self.profile=='smoke':
                await self.phase_run('smoke_reads_100',lambda:self.mixed(100,15,True))
                await self.phase_run('smoke_burst_100',self.burst)
            elif self.profile=='burst':
                for i in range(3):await self.phase_run(f'burst_100_{i+1}',self.burst)
            elif self.profile=='complex':
                for kind in ('unnamed','comparison','followup'):
                    await self.phase_run('complex_'+kind,lambda kind=kind:self.complex_questions(kind))
            else:
                await self.phase_run('baseline_1',lambda:self.mixed(1,180))
                await self.phase_run('reads_100',lambda:self.mixed(100,600,True))
                for n in (10,25,50,100):await self.phase_run(f'mixed_{n}',lambda n=n:self.mixed(n,300))
                await self.phase_run('sustained_100',lambda:self.mixed(100,1800))
                for i in range(3):await self.phase_run(f'burst_100_{i+1}',self.burst)
                for kind in ('delay','429','timeout','worker'):await self.phase_run('fault_'+kind,lambda kind=kind:self.fault(kind))
            if not self.halted:
                self.phase='persistence'
                await asyncio.sleep(3);await self.login(0)
                r=await self.request(0,'GET','/api/conversations')
                if r is None or r.status_code!=200:self.halt('RELOGIN_FAILED')
                else:
                    for conv,run,answer in self.persisted:
                        response=await self.request(0,'GET',f'/api/conversations/{conv}/messages')
                        if response is None or response.status_code!=200 or not any(m['id']==run and (m.get('answer') or {}).get('answer')==answer for m in response.json()['messages']):self.halt('RELOGIN_HISTORY_FAILED')
                if self.profile!='faults':await self.phase_run('login_peak',self.login_peak)
        finally:
            self.stop_monitor.set();await monitoring
            for client in self.clients:await client.aclose()
            write_json(self.out/'outcome.json',{'halted':self.halted,'submitted':self.submitted,'profile':self.profile,'model':'real' if self.real else 'mock'})

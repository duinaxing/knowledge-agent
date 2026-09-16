"""Exercise browser HTTP contracts against live API/worker/embedding, using synthetic data."""
import json
import time
import uuid
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import httpx

BASE='http://127.0.0.1:8000'
def client():
    return httpx.Client(base_url=BASE,headers={'Origin':'http://127.0.0.1:5173'},timeout=15)
def call(c,method,path,**kw):
    r=c.request(method,path,**kw);r.raise_for_status();return r.json()
def login(c,name,password):
    who=call(c,'POST','/api/auth/login',json={'username':name,'password':password})
    c.headers['x-csrf-token']=who['csrf']
    return who

tag=uuid.uuid4().hex[:8]
username='verify_'+tag
password=secrets.token_urlsafe(16)
new_password=secrets.token_urlsafe(16)
with client() as admin,client() as user:
    login(admin,'admin','123456')
    call(user,'POST','/api/auth/register',json={'username':username,'display_name':'验收用户 '+tag,'password':password})
    who=login(user,username,password)
    assert who['role']=='employee'
    assert user.get('/api/admin/documents').status_code==403
    topic='浏览器验收制度'+tag
    doc=call(admin,'POST','/api/admin/documents',data={'title':topic,'version_label':'v1','type':'policy'},
             files={'file':('verification.md',f'# {topic}\n合成测试制度。设备借用期限为七天，归还时须核对设备编号。'.encode(),'text/markdown')})
    call(admin,'PUT','/api/admin/resources/document/'+doc['id']+'/acl',json={'org_visible':True,'grants':[]})
    for _ in range(60):
        docs=call(admin,'GET','/api/admin/documents')
        d=next((d for d in docs if d['id']==doc['id']),None)
        if d and d['versions'][0]['processing']=='ready':break
        time.sleep(.5)
    assert d and d['versions'][0]['processing']=='ready'
    call(admin,'POST','/api/admin/versions/'+doc['version_id']+'/publish',json={'revision':d['revision'],'effective_from':datetime.now(timezone.utc).isoformat()})
    def query(i):
        start=time.monotonic()
        conv=call(user,'POST','/api/conversations')['id']
        run=call(user,'POST','/api/conversations/'+conv+'/queries',json={'message':topic+'规定设备可以借用多久？','client_request_id':uuid.uuid4().hex})
        for _ in range(130):
            result=call(user,'GET','/api/runs/'+run['run_id'])
            if result['state'] in ['completed','failed']:break
            time.sleep(.5)
        answer=result.get('answer') or {}
        assert answer.get('status')=='answered',answer.get('warnings')
        assert any(e.get('document_id')==doc['id'] for e in answer['evidence'])
        e=next(e for e in answer['evidence'] if e.get('document_id')==doc['id'])
        excerpt=call(user,'GET',f"/api/documents/{e['document_id']}/versions/{e['version_id']}/chunks/{e['chunk_id']}")
        assert '七天' in excerpt['text']
        return {'conversation_id':conv,'seconds':time.monotonic()-start,'model_calls':result['model_count'],'status':answer['status']}
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows=list(pool.map(query,range(5)))
    conv=rows[0]['conversation_id']
    call(user,'POST','/api/auth/logout')
    login(user,username,password)
    assert call(user,'GET','/api/conversations/'+conv+'/messages')['messages'][0]['answer']['evidence']
    call(user,'PATCH','/api/conversations/'+conv,json={'title':'跨登录历史验收'})
    call(user,'POST','/api/auth/password',json={'current_password':password,'new_password':new_password})
    assert user.get('/api/me').status_code==401
    assert user.post('/api/auth/login',json={'username':username,'password':password}).status_code==401
    login(user,username,new_password)
    assert call(user,'GET','/api/conversations/'+conv+'/messages')['messages']
    call(user,'POST','/api/auth/logout')
    call(admin,'POST','/api/auth/logout')
report={'date':datetime.now(timezone.utc).isoformat(),'concurrency':5,'requests':rows,
        'p95_seconds':max(r['seconds'] for r in rows),'scope':'Live HTTP registration, admin upload/index/publish, ACL denial, real cited answers, logout/login history, rename, password rotation.'}
target=Path('runtime/evaluation/account-history-live.json')
target.parent.mkdir(parents=True,exist_ok=True)
target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=True))

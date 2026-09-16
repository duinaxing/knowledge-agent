"""Small-team history read load; retains aggregate timings only."""
import json,time,statistics,math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import httpx

with httpx.Client(base_url='http://127.0.0.1:8000',headers={'Origin':'http://127.0.0.1:5173'},timeout=15,
                  limits=httpx.Limits(max_connections=20,max_keepalive_connections=20)) as c:
    r=c.post('/api/auth/login',json={'username':'employee1','password':'123456'});r.raise_for_status()
    c.headers['x-csrf-token']=r.json()['csrf']
    convs=c.get('/api/conversations').json()
    def request(i):
        path='/api/conversations' if i%2 or not convs else '/api/conversations/'+convs[0]['id']+'/messages'
        t=time.monotonic();r=c.get(path);r.raise_for_status()
        return time.monotonic()-t
    with ThreadPoolExecutor(max_workers=20) as pool:times=sorted(pool.map(request,range(100)))
    c.post('/api/auth/logout').raise_for_status()
report={'concurrency':20,'requests':100,'failures':0,'p50_seconds':statistics.median(times),
        'p95_seconds':times[math.ceil(len(times)*.95)-1],'max_seconds':times[-1],
        'scope':'Conversation lists and historical messages via live authenticated HTTP; synthetic local corpus.'}
Path('runtime/evaluation/history-load.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))

"""Owns only child processes started by this invocation; always tears them down."""
import argparse
import asyncio
import json
import os
import platform
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
import httpx
import psutil
from .common import ROOT, AREA, guard, write_json


def configure(mode, workers=4, fast_path=True):
    # Read production configuration without printing any secret, before overrides.
    from knowledge_agent.config import Settings
    original=Settings()
    from sqlalchemy.engine import make_url
    url=make_url(original.database_url)
    if url.host not in ('localhost','127.0.0.1') or not url.drivername.startswith('postgresql'):
        raise RuntimeError('This harness requires local PostgreSQL')
    upstream=original.model_api_key.get_secret_value()
    if mode=='real' and not upstream:raise RuntimeError('Real provider credential is missing')
    os.environ.update(DATABASE_URL=url.set(database='knowledge_loadtest_100').render_as_string(hide_password=False),
        FILE_ROOT=str(AREA/'files'),MODEL_MODE='real',MODEL_BASE_URL='http://127.0.0.1:8102',
        MODEL_API_KEY=secrets.token_urlsafe(24),EMBEDDING_BASE_URL='http://127.0.0.1:8101',
        EMBEDDING_MODEL='BAAI/bge-small-zh-v1.5',EMBEDDING_DIM='512',EMBEDDING_API_KEY='local-only',
        WORKER_CONCURRENCY=str(workers),SIMPLE_DOCUMENT_FAST_PATH=str(fast_path).lower(),RUN_TIMEOUT='60',MODEL_TIMEOUT='20',TOOL_TIMEOUT='5',
        LOAD_MODEL_MODE=mode,LOAD_UPSTREAM_URL=original.model_base_url,LOAD_UPSTREAM_KEY=upstream,
        PYTHONPATH=str(ROOT/'backend/src'))
    os.environ['LOAD_GATEWAY_TOKEN']=os.environ['MODEL_API_KEY']
    # The config module was imported only to read .env; replace its cached object.
    import knowledge_agent.config as config
    config.settings=Settings()
    guard()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',choices=['full','smoke','faults'],default='full')
    parser.add_argument('--real',action='store_true')
    parser.add_argument('--workers',type=int,choices=range(1,9),default=4)
    parser.add_argument('--legacy-agent',action='store_true')
    parser.add_argument('--cleanup',action='store_true')
    args=parser.parse_args()
    if args.real and args.profile=='faults':parser.error('Fault injection is simulated only')
    AREA.mkdir(parents=True,exist_ok=True)
    # Process-wide file lock prevents overlapping runs (including cleanup).
    lock=(AREA/'launch.lock').open('a+b');lock.write(b'0');lock.flush();lock.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    configure('real' if args.real else 'mock',args.workers,not args.legacy_agent)
    if args.cleanup:
        from .prepare import cleanup
        cleanup();print('Isolated test database and upload files removed; reports retained');return
    for port in (8100,8101,8102):
        with socket.socket() as check:
            try:check.bind(('127.0.0.1',port))
            except OSError:raise RuntimeError(f'Port {port} is occupied; no process was stopped') from None
    out=AREA/'results'/(time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3))
    out.mkdir(parents=True);os.environ['LOAD_RESULT_DIR']=str(out)
    write_json(AREA/'latest.json',{'directory':str(out)})
    write_json(out/'environment.json',{'platform':platform.platform(),'python':sys.version,'cpu':platform.processor(),'logical_cpus':psutil.cpu_count(),'memory_bytes':psutil.virtual_memory().total,
        'profile':args.profile,'mode':'real' if args.real else 'mock','worker_concurrency':args.workers,'simple_document_fast_path':not args.legacy_agent,'run_timeout':60,'shared_host':True})
    children=[];logs=[]
    def records():write_json(out/'processes.json',[{'pid':p.pid,'name':name} for name,p in children if p.poll() is None])
    def start(name,module,*extra):
        log=(out/(name+'.log')).open('ab');logs.append(log)
        p=subprocess.Popen([sys.executable,'-m',module,*extra],cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        children.append((name,p));records();return p
    def wait(url):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:
                r=httpx.get(url,timeout=2)
                if r.status_code==200 and r.json().get('ready',True):return
            except (httpx.HTTPError,ValueError):pass
            for name,p in children:
                if p.poll() is not None:raise RuntimeError(f'{name} exited; inspect its log')
            time.sleep(1)
        raise RuntimeError('Service startup timed out: '+url)
    worker=None
    def restart_worker():
        nonlocal worker
        if worker is None or worker.poll() is not None:raise RuntimeError('No owned worker available')
        worker.terminate();worker.wait(timeout=15)
        worker=start('worker-restarted','knowledge_loadtest.worker')
    try:
        print('Results:',out,flush=True)
        start('embedding','uvicorn','knowledge_agent.local_embeddings:app','--host','127.0.0.1','--port','8101','--no-access-log')
        wait('http://127.0.0.1:8101/health')
        prep=start('prepare','knowledge_loadtest.prepare');code=prep.wait()
        if code:raise RuntimeError('Fixture preparation failed; inspect prepare.log')
        children.remove(('prepare',prep))
        start('gateway','uvicorn','knowledge_loadtest.gateway:app','--host','127.0.0.1','--port','8102','--no-access-log')
        wait('http://127.0.0.1:8102/health')
        start('api','uvicorn','knowledge_agent.api:app','--host','127.0.0.1','--port','8100','--no-access-log')
        wait('http://127.0.0.1:8100/api/ready')
        worker=start('worker','knowledge_loadtest.worker')
        from .runner import Harness
        asyncio.run(Harness(out,args.profile,restart_worker).run())
    except BaseException as exc:
        write_json(out/'launch-error.json',{'type':type(exc).__name__})
        raise
    finally:
        for _,p in reversed(children):
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=15)
                except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
        for log in logs:log.close()
        from .report import summarize
        summarize(out)
        print('Report:',out/'REPORT.md',flush=True)


if __name__=='__main__':main()

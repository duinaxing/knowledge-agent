import asyncio
import json
import time
import os
from pathlib import Path
import psutil
from sqlalchemy import text


def database_sample():
    from knowledge_agent.db import engine
    with engine.connect() as db:
        states=dict(db.execute(text("SELECT state,count(*) FROM runs WHERE client_request_id NOT LIKE 'seed%' GROUP BY state")).all())
        active=db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"))
        locks=db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock'"))
        expired=db.scalar(text("SELECT count(*) FROM runs WHERE state IN ('queued','running','interrupted') AND deadline<extract(epoch from now())"))
    return {'states':states,'connections':active,'lock_waits':locks,'expired':expired}


async def monitor(out, stop, halt):
    processes={};low_since=None;last_db=0;cached={}
    while not stop.is_set():
        budget_path=Path(os.environ.get('VALIDATION_BUDGET_FILE',str(out/'admissions.sqlite')))
        if os.environ.get('LOAD_MODEL_MODE')=='real' and budget_path.exists():
            import sqlite3
            with sqlite3.connect(budget_path) as budget:
                try:
                    row=budget.execute("SELECT count(*) FROM admissions WHERE kind='model'").fetchone()
                    if row and row[0]>=(1500 if os.environ.get('VALIDATION_SUITE')=='quality' else 1000):halt('MODEL_BUDGET_REACHED')
                except sqlite3.OperationalError:pass
        record_path=out/'processes.json'
        records=json.loads(record_path.read_text()) if record_path.exists() else []
        stats=[]
        postgres=[]
        for p in psutil.process_iter(['pid','name']):
            if (p.info['name'] or '').lower() in ('postgres.exe','postgres'):
                postgres.append({'pid':p.pid,'name':'postgres-shared-cluster'})
        for item in records+postgres+[{'pid':__import__('os').getpid(),'name':'load-client'}]:
            try:
                p=processes.setdefault(item['pid'],psutil.Process(item['pid']))
                stats.append({'name':item['name'],'pid':p.pid,'cpu':p.cpu_percent(),'rss':p.memory_info().rss})
            except psutil.Error: pass
        now=time.time();memory=psutil.virtual_memory()
        if memory.available/memory.total<.1:
            low_since=low_since or now
            if now-low_since>=30: halt('LOW_MEMORY')
        else: low_since=None
        if now-last_db>=5:
            try: cached=await asyncio.to_thread(database_sample)
            except Exception: cached={'sample_error':True}
            last_db=now
        with (out/'resources.jsonl').open('a') as f:
            f.write(json.dumps({'at':now,'cpu':psutil.cpu_percent(),'memory_total':memory.total,'memory_available':memory.available,'processes':stats,**cached})+'\n')
        try: await asyncio.wait_for(stop.wait(),2)
        except TimeoutError: pass

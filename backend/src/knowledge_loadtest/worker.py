"""Test-only timing instrumentation, retaining the production worker algorithm."""
import json
import os
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from .common import guard


def main():
    guard()
    from knowledge_agent import worker
    from knowledge_agent import agent, models
    from knowledge_agent.db import Run
    original_claim=worker.claim
    current_run=ContextVar('loadtest_run',default=None)
    original_ask=agent.ask_model
    original_post=models._post
    def ask(state,*args,**kwargs):
        token=current_run.set(state['run_id'])
        try:return original_ask(state,*args,**kwargs)
        finally:current_run.reset(token)
    def post(base,path,key,payload,timeout):
        if base=='http://127.0.0.1:8102' and path=='/chat/completions':
            payload={**payload,'_load_run_id':current_run.get()}
        return original_post(base,path,key,payload,timeout)
    agent.ask_model=ask
    models._post=post
    lock=threading.Lock()
    def claim(cls):
        result=original_claim(cls)
        if result and cls is Run:
            with lock, (Path(os.environ['LOAD_RESULT_DIR'])/'claims.jsonl').open('a') as f:
                f.write(json.dumps({'run_id':result[0],'generation':result[1],'claimed':time.monotonic(),'at':time.time()})+'\n')
        return result
    worker.claim=claim
    worker.main()


if __name__=='__main__':main()

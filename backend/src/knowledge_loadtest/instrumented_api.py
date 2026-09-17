"""Opt-in test-instance instrumentation; never logs SQL text, bind values or input."""
from contextvars import ContextVar
import json
import os
from pathlib import Path
import time
from fastapi import Request
from sqlalchemy import event
from .common import guard
guard()
from knowledge_agent.api import app,current
from knowledge_agent.db import engine

timing=ContextVar('submission_timing',default=None)
original_connect=engine.connect


def connect(*args,**kwargs):
    started=time.perf_counter()
    try:return original_connect(*args,**kwargs)
    finally:
        row=timing.get()
        if row is not None:row['connection_acquire_seconds']+=time.perf_counter()-started


engine.connect=connect


@event.listens_for(engine,'before_cursor_execute')
def before(conn,cursor,statement,parameters,context,executemany):
    context.validation_started=time.perf_counter()


@event.listens_for(engine,'after_cursor_execute')
def after(conn,cursor,statement,parameters,context,executemany):
    row=timing.get()
    if row is not None:
        seconds=time.perf_counter()-context.validation_started
        row['sql_seconds']+=seconds;row['sql_count']+=1
        if 'FOR UPDATE' in statement.upper():row['locking_sql_seconds']+=seconds


def timed_current(request:Request):
    started=time.perf_counter()
    try:return current(request)
    finally:
        row=timing.get()
        if row is not None:row['auth_seconds']+=time.perf_counter()-started


app.dependency_overrides[current]=timed_current
for route in app.routes:
    if getattr(route,'path','')=='/api/conversations/{id_}/queries':
        original_query=route.dependant.call
        def timed_query(**kwargs):
            started=time.perf_counter()
            try:return original_query(**kwargs)
            finally:
                row=timing.get()
                if row is not None:row['task_creation_seconds']=time.perf_counter()-started
        route.dependant.call=timed_query


@app.middleware('http')
async def timings(request,call_next):
    if request.method!='POST' or not request.url.path.endswith('/queries'):return await call_next(request)
    row={key:0 for key in ('connection_acquire_seconds','sql_seconds','sql_count','locking_sql_seconds','auth_seconds','task_creation_seconds')}
    token=timing.set(row);started=time.perf_counter()
    try:
        response=await call_next(request);row['status']=response.status_code;return response
    finally:
        row['seconds']=time.perf_counter()-started;row['at']=time.time()
        row['request_id']=getattr(request.state,'request_id',None)
        timing.reset(token)
        with (Path(os.environ['LOAD_RESULT_DIR'])/'submission-timings.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')

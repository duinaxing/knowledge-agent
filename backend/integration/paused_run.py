"""Deterministic child used only by the PostgreSQL crash/recovery test."""
import sys
import time
from pathlib import Path
from langgraph.checkpoint.postgres import PostgresSaver
from knowledge_agent import models
from knowledge_agent.agent import execute
from knowledge_agent.config import settings


def pause(*args,**kwargs):
    Path(sys.argv[3]).write_text('model_call_reserved',encoding='utf-8')
    time.sleep(60)
    raise RuntimeError('Test process should have been terminated')


models.chat=pause
with PostgresSaver.from_conn_string(settings.database_url.replace('postgresql+psycopg://','postgresql://')) as saver:
    saver.setup()
    execute(sys.argv[1],int(sys.argv[2]),saver)

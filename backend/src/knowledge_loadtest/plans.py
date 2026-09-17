"""Capture representative query plans after load stops, using synthetic fixture IDs."""
from sqlalchemy import create_engine,text
from .common import guard,write_json


def capture(out):
    engine=create_engine(guard())
    statements={
        'conversation_list':"SELECT id,title,created_at FROM conversations WHERE owner_id='lu0' AND deleted_at IS NULL ORDER BY created_at DESC,id DESC LIMIT 30",
        'history':"SELECT id,state,answer FROM runs WHERE conversation_id='lc0-0' ORDER BY created_at DESC,id DESC LIMIT 51",
        'queue':"SELECT id FROM runs WHERE state='queued' ORDER BY created_at LIMIT 8",
        'conversation_lock':"SELECT id FROM conversations WHERE id='lc0-0' FOR UPDATE",
    }
    try:
        with engine.connect() as db:
            plans={name:db.execute(text('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+sql)).scalar() for name,sql in statements.items()}
            db.rollback()
        write_json(out/'query-plans.json',plans)
    finally:engine.dispose()

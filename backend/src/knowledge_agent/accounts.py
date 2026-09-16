import time
from sqlalchemy import delete
from .db import transaction, RateLimit, engine
from .security import AppError, token_hash


def throttle(subject, action, limit=30):
    """Atomic fixed-window counter shared by API processes; no raw IP stored."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    now=time.time()
    key=token_hash(f'{action}:{subject}:{int(now//60)}')
    insert=pg_insert if engine.dialect.name=='postgresql' else sqlite_insert
    with transaction() as db:
        stmt=insert(RateLimit).values(key=key,count=1,expires=now+120)
        count=db.scalar(stmt.on_conflict_do_update(index_elements=['key'],set_={'count':RateLimit.count+1}).returning(RateLimit.count))
        db.execute(delete(RateLimit).where(RateLimit.expires<now))
    if count>limit:
        raise AppError('RATE_LIMITED',429)

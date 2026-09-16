import threading
import time
from contextlib import nullcontext
from sqlalchemy import select, update, delete
from .db import transaction, Run, Job, Document, Version, Chunk, Conversation, RunEvent
from .security import AppError, epoch
from .config import settings
from .documents import parse_file, split_pages
from . import models
from .agent import execute


def sweep():
    now = time.time()
    with transaction() as db:
        db.execute(update(Run).where(Run.state == 'running', Run.lease_until < now)
                   .values(state='interrupted', error_code='WORKER_INTERRUPTED'))
        db.execute(update(Run).where(Run.state.in_(['running','interrupted','queued']), Run.deadline < now)
                   .values(state='failed', error_code='DEADLINE_EXCEEDED'))
        db.execute(update(Job).where(Job.state == 'running', Job.lease_until < now)
                   .values(state='queued', error_code='WORKER_INTERRUPTED'))


def claim(cls):
    with transaction() as db:
        row = db.scalar(select(cls).where(cls.state == 'queued').order_by(cls.created_at)
                        .with_for_update(skip_locked=True).limit(1))
        if row:
            row.state = 'running'
            row.generation += 1
            row.lease_until = time.time() + 10
            if cls is Job:
                row.attempts += 1
            return row.id, row.generation


def heartbeat(cls, id_, gen, stopped):
    while not stopped.wait(2):
        with transaction() as db:
            db.execute(update(cls).where(cls.id == id_, cls.generation == gen, cls.state == 'running')
                       .values(lease_until=time.time() + 10))


def active_job(db, id_, gen):
    job = db.scalar(select(Job).where(Job.id == id_).with_for_update())
    if not job or job.state != 'running' or job.generation != gen or job.lease_until < time.time():
        raise AppError('LEASE_LOST')
    return job


def process_job(id_, gen):
    with transaction() as db:
        job = active_job(db, id_, gen)
        if job.kind == 'cleanup':
            doc = db.get(Document, job.target_id)
            if not doc or not doc.deleted_at:
                raise AppError('INVALID_CLEANUP')
            versions = list(db.scalars(select(Version).where(Version.document_id == doc.id)))
            for v in versions:
                path = (settings.file_root / v.path).resolve()
                if path.parent != settings.file_root.resolve():
                    raise AppError('INVALID_FILE_PATH')
                path.unlink(missing_ok=True)
                db.execute(delete(Chunk).where(Chunk.version_id == v.id))
            job.state = 'completed'
            return
        version = db.get(Version, job.target_id)
        if db.get(Document, version.document_id).deleted_at:
            raise AppError('DOCUMENT_DELETED')
        version.processing = 'parsing'
    parts = split_pages(parse_file(version))
    with transaction() as db:
        active_job(db, id_, gen)
        db.get(Version, version.id).processing = 'indexing'
    vectors = []
    for offset in range(0, len(parts), 16):
        batch, _ = models.embed([p[0] for p in parts[offset:offset + 16]], 30)
        vectors.extend(batch)
    with transaction() as db:
        job = active_job(db, id_, gen)
        v = db.get(Version, version.id)
        doc = db.scalar(select(Document).where(Document.id == v.document_id).with_for_update())
        if doc.deleted_at:
            raise AppError('DOCUMENT_DELETED')
        db.execute(delete(Chunk).where(Chunk.version_id == v.id))
        for i, ((text, locator), vector) in enumerate(zip(parts, vectors, strict=True)):
            db.add(Chunk(version_id=v.id, ordinal=i, text=text, locator=locator, embedding=vector))
        v.processing, v.error_code = 'ready', None
        v.embedding_model = 'TEST_ONLY_HASH' if settings.model_mode == 'test' else settings.embedding_model
        v.embedding_dim = settings.embedding_dim
        job.state, job.error_code = 'completed', None


def work_one(cls, checkpointer=None):
    found = claim(cls)
    if not found:
        return False
    id_, gen = found
    stopped = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(cls, id_, gen, stopped), daemon=True)
    thread.start()
    try:
        execute(id_, gen, checkpointer) if cls is Run else process_job(id_, gen)
    except Exception as exc:
        code = exc.code if isinstance(exc, AppError) else 'INTERNAL_ERROR'
        with transaction() as db:
            row = db.scalar(select(cls).where(cls.id == id_).with_for_update())
            if row and row.generation == gen and row.state == 'running':
                row.state, row.error_code = 'failed', code
                if cls is Run:
                    row.answer = None
                elif row.kind == 'index':
                    v = db.get(Version, row.target_id)
                    v.processing, v.error_code = 'failed', code
    finally:
        stopped.set()
        thread.join(timeout=3)
    return True


def main():
    from langgraph.checkpoint.postgres import PostgresSaver
    from concurrent.futures import ThreadPoolExecutor
    url = settings.database_url.replace('postgresql+psycopg://', 'postgresql://')
    if url.startswith('postgresql:'):
        with PostgresSaver.from_conn_string(url) as setup:
            setup.setup()
        def consume(model):
            with PostgresSaver.from_conn_string(url) as saver:
                while True:
                    if not work_one(model,saver if model is Run else None):
                        time.sleep(.5)
        # Separate indexing capacity prevents document uploads from starving chat.
        with ThreadPoolExecutor(max_workers=settings.worker_concurrency+1) as pool:
            futures=[pool.submit(consume,Run) for _ in range(settings.worker_concurrency)]
            futures.append(pool.submit(consume,Job))
            with PostgresSaver.from_conn_string(url) as saver:
                last_cleanup=0
                while True:
                    for future in futures:
                        if future.done():future.result()
                    sweep()
                    if time.time()-last_cleanup>3600:
                        purge_expired(saver);last_cleanup=time.time()
                    time.sleep(1)
        return
    saver_context = PostgresSaver.from_conn_string(url) if url.startswith('postgresql:') else nullcontext(None)
    with saver_context as saver:
        if saver:
            saver.setup()
        last_cleanup = 0
        while True:
            sweep()
            if time.time() - last_cleanup >= 3600:
                purge_expired(saver)
                last_cleanup = time.time()
            worked = work_one(Run, saver) or work_one(Job)
            if not worked:
                time.sleep(.5)


def purge_expired(saver=None):
    """Prune obsolete execution checkpoints, retaining user conversation history."""
    from .db import Feedback
    with transaction() as db:
        convs = list(db.scalars(select(Conversation).where(Conversation.epoch != epoch(db),
                               Conversation.created_at < time.time() - 7*86400)))
        for conv in convs:
            runs = list(db.scalars(select(Run).where(Run.conversation_id == conv.id)))
            for run in runs:
                if saver:
                    for gen in range(1, run.generation + 1):
                        saver.delete_thread(run.id + ':' + str(gen))
                for f in db.scalars(select(Feedback).where(Feedback.run_id == run.id)):
                    f.note = '[已清理]'
            conv.project_id, conv.candidates = None, []


if __name__ == '__main__':
    main()

"""Explicit preparation and guarded cleanup. No business seed/reset code used."""
import argparse
import json
import random
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import create_engine, text, select, func
from .common import AREA, ROOT, DB_NAME, guard, pdf_bytes, write_json


def create_database():
    url = guard()
    engine = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with engine.connect() as db:
        exists = db.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'), {'name': DB_NAME})
        if not exists: db.execute(text(f'CREATE DATABASE {DB_NAME}'))
    engine.dispose()


def cleanup():
    url = guard()
    engine = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with engine.connect() as db:
        if db.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE datname=:name'), {'name': DB_NAME}):
            raise RuntimeError('Stop test services before cleanup; active database sessions exist')
        db.execute(text(f'DROP DATABASE IF EXISTS {DB_NAME}'))
    engine.dispose()
    # Delete generated upload files only. Keep credentials/reports for provenance.
    folder = (AREA / 'files').resolve()
    for item in folder.glob('*'):
        if item.is_symlink() or item.resolve().parent != folder or not item.is_file():
            raise RuntimeError('Unsafe cleanup entry')
        item.unlink()
    (AREA / 'fixture.json').unlink(missing_ok=True)


def prepare():
    guard(); create_database()
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT / 'backend/alembic.ini')), 'head')
    from knowledge_agent.db import transaction, User, Group, Membership, Document, Version, Chunk, ACL, SystemState, Conversation, Run, Job
    from knowledge_agent.security import password_hash, epoch
    from knowledge_agent.documents import upload, publish
    from knowledge_agent.schemas import Publish
    from knowledge_agent.worker import work_one
    with transaction() as db:
        count = db.scalar(select(func.count()).select_from(User))
    if count:
        if not (AREA / 'fixture.json').exists(): raise RuntimeError('Partial fixture found: use guarded cleanup before preparing again')
        print('Reusing completed isolated fixture', flush=True); return
    password = secrets.token_urlsafe(18)
    write_json(AREA / 'credentials.json', {'password': password})
    rng = random.Random(20260915)
    with transaction() as db:
        if not db.get(SystemState,1):db.add(SystemState(id=1, epoch=1))
        for g in range(10): db.add(Group(id=f'lg{g}', name=f'Load group {g}'))
        for u in range(100):
            db.add(User(id=f'lu{u}', username=f'load{u:03}', display_name=f'Load employee {u:03}', role='employee', password_hash=password_hash(password)))
        db.flush()
        for u in range(100): db.add(Membership(user_id=f'lu{u}', group_id=f'lg{u%10}'))
    fixture = {'users': [], 'documents': [], 'seed': 20260915}
    for i in range(300):
        scope = i // 100
        title = f'TESTDOC-{i:03}'
        code = f'VERIFY-{rng.randrange(10000000,99999999)}'
        allowed = list(range(100)) if scope == 0 else [u for u in range(100) if u%10 == i%10] if scope == 1 else [i%100]
        suffix = ['.md','.txt','.pdf'][i%3]
        lines = [f'{title} operational specification', f'Verification code: {code}.', 'Synthetic fixture; not real business information.']
        lines += [f'Section {n:02}: {title} requires recorded approval, review and audit evidence.' for n in range(1,18)]
        with transaction() as db:
            db.add(Document(id=f'ld{i}', title=title, org_visible=scope==0)); db.flush()
            if scope:
                db.add(ACL(resource_type='document',resource_id=f'ld{i}',subject_type='group' if scope==1 else 'user',subject_id=f'lg{i%10}' if scope==1 else f'lu{i%100}'))
            labels = ['v0','v1'] if i%10==0 else ['v1']
            for label in labels:
                content = lines if label=='v1' else [s.replace(code,'OBSOLETE-CODE') for s in lines]
                raw = pdf_bytes(content) if suffix=='.pdf' else ('\n'.join(content)).encode()
                upload(db,f'ld{i}',label,title+suffix,raw)
        fixture['documents'].append({'id':f'ld{i}','title':title,'code':code,'allowed':allowed,'suffix':suffix})
    processed = 0
    while work_one(Job):
        processed += 1
        if processed%20 == 0: print(f'Indexed {processed}/330 versions', flush=True)
    with transaction() as db:
        versions = list(db.scalars(select(Version).order_by(Version.document_id,Version.version_label)))
        if len(versions)!=330 or any(v.processing!='ready' for v in versions): raise RuntimeError('Fixture indexing failed; inspect isolated jobs')
        for v in versions:
            doc=db.get(Document,v.document_id)
            publish(db,v.id,Publish(revision=doc.revision,effective_from=datetime(2026,1 if v.version_label=='v0' else 9,1,tzinfo=timezone.utc)))
        db.flush()
        for d in fixture['documents']:
            v=next(v for v in versions if v.document_id==d['id'] and v.version_label=='v1')
            chunks=list(db.scalars(select(Chunk).where(Chunk.version_id==v.id).order_by(Chunk.ordinal)))
            d.update(version_id=v.id,chunk_id=chunks[0].id,target_chunks=[c.id for c in chunks if d['code'] in c.text])
        current_epoch=epoch(db)
        for u in range(100):
            convs=[]
            for c in range(10):
                id_=f'lc{u}-{c}';convs.append(id_)
                db.add(Conversation(id=id_,owner_id=f'lu{u}',epoch=current_epoch,title=f'Fixture conversation {c}'))
            db.flush()
            for c in convs:
                db.add_all([Run(id=f'{c}-r{n}',conversation_id=c,owner_id=f'lu{u}',client_request_id=f'seed{n}',message='Synthetic historical question',epoch=current_epoch,state='completed',deadline=time.time(),answer={'status':'answered','kind':'general','answer':f'Historical record {n}','facts':[],'evidence':[],'warnings':[],'access_scope':[]}) for n in range(20)])
            fixture['users'].append({'index':u,'id':f'lu{u}','username':f'load{u:03}','conversations':convs})
    write_json(AREA/'fixture.json',fixture)
    print('Fixture ready: 100 users, 300 documents, 330 indexed versions, 20000 historical turns',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--cleanup',action='store_true');args=parser.parse_args()
    cleanup() if args.cleanup else prepare()

"""Offline, allowlisted recovery drill. Never restores over an existing database."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime,timezone
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
import uuid

ROOT=Path(__file__).resolve().parents[3]
AREA=ROOT/'runtime/recovery'
TARGET='knowledge_restore_test'
SOURCES={'knowledge_loadtest_100':'loadtest','knowledge_quality_test':'quality'}
TABLES=('users','groups','user_groups','projects','documents','document_versions','chunks','conversations','runs','acl_entries')


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inside(path,parent):
    path=Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(Path(parent).resolve()):raise RuntimeError('Unsafe path')
    return path.resolve()


@contextmanager
def source_lock(suite):
    path=ROOT/'runtime'/suite/'launch.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as handle:
        handle.write(b'0');handle.flush();handle.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def validate(bundle):
    if not AREA.resolve().is_relative_to(ROOT.resolve()):raise RuntimeError('Recovery root escapes workspace')
    bundle=inside(bundle,AREA)
    manifest=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('source_database') not in SOURCES or manifest.get('schema_version')!=1:raise RuntimeError('Unsupported backup')
    for id_,path in manifest.get('version_paths',{}).items():
        if path!=id_ or not id_.replace('-','').isalnum() or 'files/'+id_ not in manifest['files']:raise RuntimeError('Invalid document path mapping')
    for name,sha in manifest['files'].items():
        path=inside(bundle/name,bundle)
        if not path.is_file() or digest(path)!=sha:raise RuntimeError('Backup missing or corrupt: '+name)
    if 'database.dump' not in manifest['files']:raise RuntimeError('Database dump missing')
    return manifest


def pg(binary,url,*args):
    executable=ROOT/'runtime/postgres/Library/bin'/f'{binary}.exe'
    command=[str(executable),'-h',url.host,'-p',str(url.port or 5432),'-U',url.username,'-d',url.database,*args]
    result=subprocess.run(command,env={**os.environ,'PGPASSWORD':url.password or ''},capture_output=True)
    if result.returncode:raise RuntimeError(binary+' failed (credentials/output suppressed)')


def counts(connection):return {table:connection.scalar(text(f'SELECT count(*) FROM {table}')) for table in TABLES}


def preserved_data(connection):
    """Compare business data independently of paths and intentionally invalidated leases."""
    queries={
        'completed_answers':"SELECT id,conversation_id,owner_id,message,answer FROM runs WHERE state='completed' ORDER BY id",
        'documents':"SELECT id,title,deleted_at,org_visible FROM documents ORDER BY id",
        'versions':"SELECT id,document_id,version_label,content_hash,publication,effective_from,effective_to FROM document_versions ORDER BY id",
        'memberships':"SELECT user_id,group_id FROM user_groups ORDER BY user_id,group_id",
        'permissions':"SELECT resource_type,resource_id,subject_type,subject_id FROM acl_entries ORDER BY resource_type,resource_id,subject_type,subject_id",
        'chunks':"SELECT id,version_id,text,locator FROM chunks ORDER BY id",
    }
    return {name:hashlib.sha256(json.dumps([list(row) for row in connection.execute(text(sql))],sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest() for name,sql in queries.items()}


@contextmanager
def interrupted_fixture(url):
    """Temporarily seed an interrupted run and deleted document in the isolated source."""
    if url.database not in SOURCES or url.host not in ('127.0.0.1','localhost'):raise RuntimeError('Unsafe drill source')
    from sqlalchemy.orm import Session
    from knowledge_agent.db import Conversation,Document,Run,User,SystemState
    engine=create_engine(url);marker='restore-drill-'+uuid.uuid4().hex
    try:
        with Session(engine) as db,db.begin():
            if db.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()')):raise RuntimeError('Stop source services before drill')
            owner=db.query(User).order_by(User.id).first();state=db.get(SystemState,1)
            db.add(Conversation(id=marker,owner_id=owner.id,epoch=state.epoch,title='Recovery drill'));db.flush()
            db.add(Run(id=marker,conversation_id=marker,owner_id=owner.id,epoch=state.epoch,client_request_id=marker,message='Synthetic interrupted task',state='interrupted',deadline=time.time()+60,generation=2))
            db.add(Document(id=marker,title='Synthetic deleted document',org_visible=True,deleted_at=time.time()))
        engine.dispose()
        yield marker
    finally:
        with engine.begin() as db:
            db.execute(text('DELETE FROM runs WHERE id=:id'),{'id':marker})
            db.execute(text('DELETE FROM conversations WHERE id=:id'),{'id':marker})
            db.execute(text('DELETE FROM documents WHERE id=:id'),{'id':marker})
        engine.dispose()


def sanitize(connection):
    connection.execute(text('DELETE FROM auth_sessions'))
    connection.execute(text("UPDATE runs SET state='failed',error_code='RESTORED_REQUIRES_RETRY',generation=generation+1,lease_until=NULL WHERE state IN ('queued','running','interrupted')"))
    connection.execute(text("UPDATE jobs SET state='failed',error_code='RESTORED_REQUIRES_RETRY',generation=generation+1,lease_until=NULL WHERE state IN ('queued','running','interrupted')"))


def backup(url):
    if url.host not in ('127.0.0.1','localhost') or url.database not in SOURCES:raise RuntimeError('Refusing non-test backup')
    files=ROOT/'runtime'/SOURCES[url.database]/'files'
    inside(files,ROOT)
    inside(AREA,ROOT)
    destination=AREA/('backup-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    destination.mkdir(parents=True,exist_ok=False);(destination/'files').mkdir()
    engine=create_engine(url)
    try:
        with engine.connect() as db:
            # Caller must stop owned test API/worker/embedding before the drill.
            active=db.scalar(text('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()'))
            if active:raise RuntimeError('Test database still in use; stop its test services first')
            # SHARE locks exclude all table writes through the snapshot and file copy.
            names=list(db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
            for name in sorted(names):
                if not name.replace('_','').isalnum():raise RuntimeError('Unexpected table name')
                db.execute(text(f'LOCK TABLE "{name}" IN SHARE MODE'))
            manifest={'schema_version':1,'source_database':url.database,'consistent_at':datetime.now(timezone.utc).isoformat(),
                      'counts':counts(db),'preserved_data':preserved_data(db),'migration':db.scalar(text('SELECT version_num FROM alembic_version')),
                      'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                      'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT)),
                      'embedding':list(map(list,db.execute(text('SELECT DISTINCT embedding_model,embedding_dim FROM document_versions')).all())),
                      'files':{},'version_paths':{}}
            pg('pg_dump',url,'-Fc','-f',str(destination/'database.dump'))
            for row in db.execute(text('SELECT id,path,content_hash FROM document_versions')):
                source=inside(files/row.path,files)
                if not source.is_file():
                    deleted=db.scalar(text('SELECT deleted_at FROM documents WHERE id=(SELECT document_id FROM document_versions WHERE id=:id)'),{'id':row.id})
                    if deleted:continue
                    raise RuntimeError('Active original file missing')
                if digest(source)!=row.content_hash:raise RuntimeError('Source content hash mismatch')
                target=destination/'files'/row.id
                shutil.copyfile(source,target)
                manifest['version_paths'][row.id]=row.id
                manifest['files']['files/'+row.id]=digest(target)
            manifest['files']['database.dump']=digest(destination/'database.dump')
            (destination/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:engine.dispose()
    validate(destination)
    return destination


def restore(bundle,url):
    manifest=validate(bundle)
    if url.database!=TARGET or url.host not in ('127.0.0.1','localhost') or url.drivername!='postgresql+psycopg':raise RuntimeError('Unsafe restore target')
    target_files=inside(AREA/'restored-files',AREA)
    admin=create_engine(url.set(database='postgres'),isolation_level='AUTOCOMMIT')
    try:
        with admin.connect() as db:
            if target_files.exists() or db.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'),{'name':TARGET}):raise RuntimeError('Restore target exists; refusing overwrite')
            db.execute(text(f'CREATE DATABASE {TARGET}'))
    finally:admin.dispose()
    target_files.mkdir(parents=True)
    for id_ in manifest['version_paths']:
        shutil.copyfile(inside(Path(bundle)/'files'/id_,Path(bundle)/'files'),inside(target_files/id_,target_files))
    pg('pg_restore',url,'--no-owner','--no-privileges','--exit-on-error',str(Path(bundle)/'database.dump'))
    engine=create_engine(url)
    try:
        with engine.begin() as db:
            if counts(db)!=manifest['counts']:raise RuntimeError('Restored row counts differ')
            for id_,path in manifest['version_paths'].items():db.execute(text('UPDATE document_versions SET path=:path WHERE id=:id'),{'path':path,'id':id_})
            sanitize(db)
            if preserved_data(db)!=manifest['preserved_data']:raise RuntimeError('Restored histories, versions or permissions differ')
            if db.scalar(text('SELECT count(*) FROM auth_sessions')):raise RuntimeError('Old sessions remain')
            if db.scalar(text("SELECT count(*) FROM runs WHERE state IN ('queued','running','interrupted')")):raise RuntimeError('Replayable runs remain')
            versions=db.execute(text('SELECT DISTINCT vector_dims(embedding) FROM chunks')).scalars().all()
            if versions!=[512]:raise RuntimeError('Vector dimensions differ')
    finally:engine.dispose()
    return manifest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',choices=list(SOURCES),default='knowledge_loadtest_100');parser.add_argument('--verify',type=Path)
    args=parser.parse_args()
    if args.verify:validate(args.verify);print('Backup integrity verified');return
    from knowledge_agent.config import Settings
    url=make_url(Settings().database_url).set(database=args.source)
    started=time.monotonic()
    report={'source':args.source,'target':TARGET,'stage':'backup','http_checks_passed':False,'production_rpo':None}
    try:
        with source_lock(SOURCES[args.source]),interrupted_fixture(url) as marker:bundle=backup(url)
        report.update(backup=str(bundle),stage='restore',marker=marker)
        manifest=restore(bundle,url.set(database=TARGET))
        report.update(stage='http_checks',restore_point=manifest['consistent_at'])
        environment={**os.environ,'DATABASE_URL':url.set(database=TARGET).render_as_string(hide_password=False),'FILE_ROOT':str(AREA/'restored-files'),
                     'RECOVERY_SOURCE':SOURCES[args.source],'RECOVERY_MARKER':marker,'MODEL_MODE':'real','EMBEDDING_DIM':'512','EMBEDDING_MODEL':'BAAI/bge-small-zh-v1.5',
                     'EMBEDDING_BASE_URL':os.environ.get('RECOVERY_EMBEDDING_URL','http://127.0.0.1:8001')}
        result=subprocess.run([__import__('sys').executable,'-m','knowledge_quality.recovery_check'],env=environment,cwd=ROOT,capture_output=True,text=True)
        if result.returncode:raise RuntimeError('Restored HTTP verification failed; see recovery report')
        report.update(stage='complete',http_checks_passed=True)
    except Exception as exc:
        report['error_type']=type(exc).__name__
        raise
    finally:
        seconds=time.monotonic()-started;report.update(seconds=seconds,within_15_minutes=seconds<=900)
        AREA.mkdir(parents=True,exist_ok=True)
        encoded=json.dumps(report,indent=2)
        (AREA/'report.json').write_text(encoded,encoding='utf-8')
        (AREA/'REPORT.md').write_text('# Recovery drill\n\n'+encoded+'\n\nTarget retained for inspection. Existing target is never overwritten. No production RPO commitment.\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()

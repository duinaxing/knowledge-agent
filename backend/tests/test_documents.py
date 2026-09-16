import time
from datetime import datetime, timezone
import pytest
from knowledge_agent.db import transaction, Document, Version, Chunk, Job
from knowledge_agent.security import AppError, identity, epoch
from knowledge_agent.documents import publish, upload, delete_document, excerpt, parse_file
from knowledge_agent.schemas import Publish, Search
from knowledge_agent.retrieval import search
from knowledge_agent.worker import claim, process_job, work_one
from sqlalchemy import select, func


def draft(db,id_='v3'):
    db.add(Version(id=id_,document_id='d1',version_label=id_,content_hash='x',path=id_,suffix='.md',processing='ready'))
    db.flush()


def test_et13_publish_revision_and_interval():
    with transaction() as db:
        draft(db)
        publish(db,'v3',Publish(revision=1,effective_from=datetime.fromtimestamp(100,tz=timezone.utc)))
        assert db.get(Version,'v1').effective_to==100
        assert epoch(db)==2
    with transaction() as db:
        draft(db,'v4')
        with pytest.raises(AppError):publish(db,'v4',Publish(revision=1,effective_from=datetime.now(timezone.utc)))


def test_et14_current_history():
    with transaction() as db:
        db.get(Version,'v1').effective_to=2
    with transaction() as db:
        assert search(db,identity(db,'u1'),Search(query='验收'),1)[0]==[]
        assert search(db,identity(db,'u1'),Search(query='验收',mode='history'),1)[0][0]['version_id']=='v1'


def test_et15_parse_failure_preserves_old(monkeypatch):
    with transaction() as db:upload(db,'d1','v3','bad.txt',b'\xff')
    work_one(Job)
    with transaction() as db:
        assert db.get(Version,'v1').processing=='ready'
        v=db.scalar(select(Version).where(Version.version_label=='v3'))
        assert v.processing=='failed'


def test_et16_embedding_failure(monkeypatch):
    from knowledge_agent import models
    monkeypatch.setattr(models,'embed',lambda *a: (_ for _ in ()).throw(AppError('EMBEDDING_DIMENSION_MISMATCH')))
    with transaction() as db:upload(db,'d1','v3','new.md','新内容'.encode())
    work_one(Job)
    with transaction() as db:
        v=db.scalar(select(Version).where(Version.version_label=='v3'))
        assert v.processing=='failed' and v.publication=='draft'
        assert db.get(Version,'v1').publication=='published'


def test_et17_duplicate_index_idempotent():
    with transaction() as db:v=upload(db,'d1','v3','new.md','验收资料'.encode());id_=v.id
    assert work_one(Job)
    with transaction() as db:db.add(Job(kind='index',target_id=id_))
    assert work_one(Job)
    with transaction() as db:
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.version_id==id_))==1


def test_et18_deleted_document_denied(employee):
    with transaction() as db:delete_document(db,'d1')
    assert employee.get('/api/documents/d1/versions/v1/chunks/c1').status_code==404
    with transaction() as db:assert search(db,identity(db,'u1'),Search(query='验收'),2)[0]==[]


def test_et19_cleanup_failure_visible_retry(administrator,monkeypatch):
    from pathlib import Path
    with transaction() as db:delete_document(db,'d1')
    monkeypatch.setattr(Path,'unlink',lambda *a,**kw: (_ for _ in ()).throw(OSError('denied')))
    work_one(Job)
    with transaction() as db:
        job=db.scalar(select(Job).where(Job.kind=='cleanup'))
        assert job.state=='failed' and db.get(Document,'d1').deleted_at
        id_=job.id
    assert administrator.post(f'/api/admin/jobs/{id_}/retry').status_code==200


@pytest.mark.parametrize('name,body,code',[('x.docx',b'hi','UNSUPPORTED_FORMAT'),('x.md',b'','INVALID_FILE_SIZE'),('x.txt',b'a'*(10*1024*1024+1),'INVALID_FILE_SIZE')], ids=['unsupported','empty','oversized'])
def test_et20_upload_limits(name,body,code):
    with transaction() as db:
        with pytest.raises(AppError) as e:upload(db,'d1','v3',name,body)
        assert e.value.code==code


def test_scanned_pdf_fails():
    from pypdf import PdfWriter
    from io import BytesIO
    stream=BytesIO();writer=PdfWriter();writer.add_blank_page(100,100);writer.write(stream)
    with transaction() as db:v=upload(db,'d1','v3','scan.pdf',stream.getvalue())
    with pytest.raises(AppError):parse_file(v)


def test_publish_not_ready(administrator):
    with transaction() as db:v=upload(db,'d1','v3','x.md',b'hello');id_=v.id
    r=administrator.post(f'/api/admin/versions/{id_}/publish',json={'revision':1,'effective_from':'2026-09-12T00:00:00Z'})
    assert r.status_code==409


def test_index_model_mismatch():
    with transaction() as db:db.get(Version,'v1').embedding_model='different'
    with transaction() as db:
        with pytest.raises(AppError) as e:search(db,identity(db,'u1'),Search(query='验收'),1)
        assert e.value.code=='INDEX_MODEL_MISMATCH'

"""Fresh-process HTTP validation against the restored instance, never production."""
import json
import os
from sqlalchemy import select,text
from fastapi.testclient import TestClient
from .recovery import ROOT,TARGET,AREA
from sqlalchemy.engine import make_url
from knowledge_agent.config import settings
if make_url(settings.database_url).database!=TARGET or settings.file_root.resolve()!=(AREA/'restored-files').resolve():
    raise RuntimeError('Unsafe restore verification')
from knowledge_agent.api import app
from knowledge_agent.db import transaction,Conversation,Document,Version,Chunk,User,Run
from knowledge_agent.security import identity,epoch,password_hash
from knowledge_agent.retrieval import search
from knowledge_agent.schemas import Search


def main():
    suite=os.environ['RECOVERY_SOURCE'];username='load000' if suite=='loadtest' else 'quality0';user_id='lu0' if suite=='loadtest' else username
    password=json.loads((ROOT/'runtime'/suite/'credentials.json').read_text())['password']
    with transaction() as db:
        marker=os.environ['RECOVERY_MARKER'];run=db.get(Run,marker)
        assert run.state=='failed' and run.error_code=='RESTORED_REQUIRES_RETRY' and run.generation==3
        assert db.get(Document,marker).deleted_at is not None
    with TestClient(app) as client:
        client.headers['origin']='http://127.0.0.1:5173'
        assert client.get('/api/me').status_code==401
        response=client.post('/api/auth/login',json={'username':username,'password':password});assert response.status_code==200
        client.headers['x-csrf-token']=response.json()['csrf']
        assert client.get('/api/conversations').status_code==200
        with transaction() as db:
            for conv in list(db.scalars(select(Conversation).where(Conversation.owner_id==user_id)))[:5]:
                response=client.get(f'/api/conversations/{conv.id}/messages');assert response.status_code==(404 if conv.deleted_at else 200)
            query='TESTDOC-000 verification code' if suite=='loadtest' else '青岚验收码'
            evidence,_=search(db,identity(db,user_id),Search(query=query),epoch(db))
            assert evidence
            for e in evidence:
                assert client.get(f"/api/documents/{e['document_id']}/versions/{e['version_id']}/chunks/{e['chunk_id']}").status_code==200
            fixture=json.loads((ROOT/'runtime'/suite/'fixture.json').read_text(encoding='utf-8'))
            if suite=='loadtest':
                hidden=next(d for d in fixture['documents'] if 0 not in d['allowed'])
                version,chunk=hidden['version_id'],hidden['chunk_id']
            else:
                hidden=next(d for d in fixture['documents'] if not d['public']);version=hidden['version_id'];chunk=hidden['chunks'][0]['id']
            assert client.get(f"/api/documents/{hidden['id']}/versions/{version}/chunks/{chunk}").status_code==404
        # A synthetic administrator exists only in the restore target, after snapshot checks.
        with transaction() as db:
            if not db.get(User,'restore-admin'):
                db.add(User(id='restore-admin',username='restore-admin',display_name='Restore verifier',role='admin',password_hash=password_hash(password)))
            versions=list(db.execute(select(Version.id,Version.document_id).join(Document,Document.id==Version.document_id).where(Document.deleted_at.is_(None))).all())
        response=client.post('/api/auth/login',json={'username':'restore-admin','password':password});assert response.status_code==200
        client.headers['x-csrf-token']=response.json()['csrf']
        for version_id,document_id in versions:
            content=client.get(f'/api/admin/documents/{document_id}/versions/{version_id}/content')
            assert content.status_code==200 and content.json()['chunks']
    print('Restored login, histories, vector search, excerpts and ACL verified')


if __name__=='__main__':main()

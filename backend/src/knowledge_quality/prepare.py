import secrets
from datetime import datetime
from sqlalchemy import select,func
from knowledge_loadtest.common import AREA,ROOT,SUITE,guard,write_json
from knowledge_loadtest.prepare import create_database
from .dataset import load,fingerprint


def main():
    if SUITE!='quality':raise RuntimeError('Quality isolation required')
    guard();create_database()
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')
    from knowledge_agent.db import transaction,User,Group,Membership,Document,Version,Chunk,ACL,Job
    from knowledge_agent.security import password_hash
    from knowledge_agent.documents import upload,publish
    from knowledge_agent.schemas import Publish
    from knowledge_agent.worker import work_one
    data=load();sha=fingerprint(data)
    with transaction() as db:count=db.scalar(select(func.count()).select_from(User))
    if count:
        import json
        marker=AREA/'fixture.json'
        if not marker.exists() or json.loads(marker.read_text())['dataset_sha256']!=sha:raise RuntimeError('Partial or incompatible fixture; guarded cleanup required')
        return
    password=secrets.token_urlsafe(20);write_json(AREA/'credentials.json',{'password':password})
    with transaction() as db:
        db.add(Group(id='quality-group',name='Quality group'))
        for u in range(2):db.add(User(id=f'quality{u}',username=f'quality{u}',display_name=f'Quality {u}',role='employee',password_hash=password_hash(password)))
        db.flush();db.add(Membership(user_id='quality0',group_id='quality-group'))
        for item in data['documents']:
            db.add(Document(id=item['id'],title=item['title'],org_visible=False));db.flush()
            db.add(ACL(resource_type='document',resource_id=item['id'],subject_type='group' if item['public'] else 'user',subject_id='quality-group' if item['public'] else 'quality1'))
            for v in item['versions']:upload(db,item['id'],v['label'],item['title']+'.md',v['body'].encode())
    while work_one(Job):pass
    fixture={'dataset_sha256':sha,'documents':[]}
    with transaction() as db:
        for item in data['documents']:
            for v in item['versions']:
                version=db.scalar(select(Version).where(Version.document_id==item['id'],Version.version_label==v['label']))
                if version.processing!='ready':raise RuntimeError('Indexing failed')
                publish(db,version.id,Publish(revision=db.get(Document,item['id']).revision,effective_from=datetime.fromisoformat(v['from'])))
                chunks=list(db.scalars(select(Chunk).where(Chunk.version_id==version.id)))
                fixture['documents'].append({'id':item['id'],'version_id':version.id,'version_label':v['label'],
                    'public':item['public'],'chunks':[{'id':c.id,'text':c.text,'locator':c.locator} for c in chunks]})
    write_json(AREA/'fixture.json',fixture)


if __name__=='__main__':main()

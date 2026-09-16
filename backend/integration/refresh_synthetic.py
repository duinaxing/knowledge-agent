"""Add new versions to this task's synthetic fixture, without deleting old versions."""
from datetime import datetime,timezone
import time
from sqlalchemy import select
from knowledge_agent.db import transaction,Document,Version,Job,Project
from knowledge_agent.seed import TITLES
from knowledge_agent.synthetic_content import content
from knowledge_agent.documents import upload,publish
from knowledge_agent.security import bump_epoch
from knowledge_agent.worker import work_one
from knowledge_agent.schemas import Publish

with transaction() as db:
    for i in range(1,7):
        p=db.get(Project,f'p{i}')
        if not p or p.code!=f'PRJ-{i:03}':raise RuntimeError('Not the expected synthetic corpus')
        for j,title in enumerate(TITLES,1):
            if i<5 and j!=10:continue
            d=db.get(Document,f'd{i:02}{j:02}')
            if not d or not d.title.startswith(p.code):raise RuntimeError('Synthetic document mismatch')
            if db.scalar(select(Version.id).where(Version.document_id==d.id,Version.version_label=='v2-topic')):continue
            if j==10:
                d.project_id=None;d.type='policy';d.title=f'{p.code} {p.name}·{title}'
                bump_epoch(db)
            upload(db,d.id,'v2-topic','synthetic.md',content(i,j,title,p.code,p.name).encode())
while work_one(Job):pass
# A separately running worker may already own the final indexing lease.
deadline=time.monotonic()+120
while True:
    with transaction() as db:
        states=list(db.scalars(select(Version.processing).where(Version.version_label=='v2-topic')))
    if all(s=='ready' for s in states):break
    if 'failed' in states or time.monotonic()>=deadline:raise RuntimeError('Synthetic indexing failed or timed out')
    time.sleep(1)
with transaction() as db:
    for v in db.scalars(select(Version).where(Version.version_label=='v2-topic',Version.publication=='draft')):
        if v.processing!='ready':raise RuntimeError('Synthetic indexing failed')
        doc=db.get(Document,v.document_id)
        publish(db,v.id,Publish(revision=doc.revision,effective_from=datetime(2026,9,10,tzinfo=timezone.utc)))
print('Synthetic topic revision published; older versions retained for history tests.')

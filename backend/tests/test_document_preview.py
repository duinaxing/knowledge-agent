from knowledge_agent.db import transaction, Version, Document, Chunk


def test_admin_reads_draft_and_paginates(administrator):
    with transaction() as db:
        db.get(Version,'v1').publication='draft'
        db.add(Chunk(id='extra',version_id='v1',ordinal=1,text='后续正文',locator={'page':2},embedding=[0.0]*16))
    path='/api/admin/documents/d1/versions/v1/content'
    first=administrator.get(path+'?limit=1')
    assert first.status_code==200
    assert first.json()['version']['publication']=='draft'
    assert first.json()['has_more'] and len(first.json()['chunks'])==1
    second=administrator.get(path+'?limit=1&offset=1').json()
    assert second['chunks'][0]['text']=='后续正文' and not second['has_more']


def test_employee_cannot_preview(employee):
    assert employee.get('/api/admin/documents/d1/versions/v1/content').status_code==403


def test_preview_rejects_wrong_document_and_deleted(administrator):
    assert administrator.get('/api/admin/documents/d1/versions/v2/content').status_code==404
    with transaction() as db:db.get(Document,'d1').deleted_at=1
    assert administrator.get('/api/admin/documents/d1/versions/v1/content').status_code==404

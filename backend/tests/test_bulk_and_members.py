from sqlalchemy import select
from knowledge_agent.db import transaction, Document, Job, Membership, User


def test_bulk_delete_atomic_and_idempotent(administrator):
    url = '/api/admin/documents/bulk-delete'
    assert administrator.post(url, json={'document_ids':['d1','missing']}).status_code == 404
    with transaction() as db:
        assert db.get(Document, 'd1').deleted_at is None
    for _ in range(2):
        assert administrator.post(url, json={'document_ids':['d2','d1','d1']}).status_code == 200
    with transaction() as db:
        assert all(db.get(Document, id_).deleted_at for id_ in ['d1','d2'])
        assert len(list(db.scalars(select(Job).where(Job.kind == 'cleanup')))) == 2


def test_bulk_requires_admin_and_selection(employee):
    assert employee.post('/api/admin/documents/bulk-delete', json={'document_ids':['d1']}).status_code == 403


def test_bulk_invalid_selection(administrator):
    for ids in [[], ['d1'] * 1001]:
        assert administrator.post('/api/admin/documents/bulk-delete', json={'document_ids':ids}).status_code == 422


def test_document_list_excludes_deleted_before_pagination(administrator):
    with transaction() as db:
        db.get(Document, 'd1').title = 'A deleted'
        db.get(Document, 'd2').title = 'B active'
    assert administrator.delete('/api/admin/documents/d1').status_code == 200
    page = administrator.get('/api/admin/documents?limit=1').json()
    assert [doc['id'] for doc in page] == ['d2']
    assert administrator.get('/api/admin/documents?limit=1&offset=1').json() == []
    assert administrator.post('/api/admin/documents/bulk-delete', json={'document_ids':['d2']}).status_code == 200
    assert administrator.get('/api/admin/documents').json() == []


def test_individual_members_preserve_others_and_account(administrator):
    url = '/api/admin/groups/g1/members/u2'
    for _ in range(2):
        assert administrator.put(url).status_code == 200
    with transaction() as db:
        assert set(db.scalars(select(Membership.user_id).where(Membership.group_id == 'g1'))) == {'u1','u2'}
    for _ in range(2):
        assert administrator.delete(url).status_code == 200
    with transaction() as db:
        assert set(db.scalars(select(Membership.user_id).where(Membership.group_id == 'g1'))) == {'u1'}
        assert db.get(User, 'u2') is not None
    assert administrator.put('/api/admin/groups/g1/members/missing').status_code == 400


def test_employee_cannot_change_members(employee):
    url = '/api/admin/groups/g1/members/u2'
    assert employee.put(url).status_code == 403
    assert employee.delete(url).status_code == 403

from knowledge_agent.db import transaction, Project
from sqlalchemy.exc import IntegrityError
import pytest

def test_deleted_code_can_be_reused(administrator):
    assert administrator.delete('/api/admin/projects/p1').status_code==200
    response=administrator.post('/api/admin/projects',json={'code':'P1','name':'新项目','summary':'新内容'})
    assert response.status_code==201
    assert response.json()['id']!='p1'
    with transaction() as db:
        assert db.get(Project,'p1').deleted_at is not None

def test_active_duplicate_has_specific_error(administrator):
    response=administrator.post('/api/admin/projects',json={'code':'P1','name':'重复项目'})
    assert response.status_code==409
    assert response.json()['error_code']=='PROJECT_CODE_EXISTS'
    with pytest.raises(IntegrityError):
        with transaction() as db:db.add(Project(code='P1',name='并发重复'))

def test_edit_cannot_take_active_code(administrator):
    response=administrator.patch('/api/admin/projects/p2',json={'code':'P1','name':'修改项目','revision':1})
    assert response.status_code==409
    assert response.json()['error_code']=='PROJECT_CODE_EXISTS'

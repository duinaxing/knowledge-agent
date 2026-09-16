from sqlalchemy import select,func
from knowledge_agent.db import transaction,Group,User,Membership,ACL
from knowledge_agent.security import epoch,identity,can_read
from knowledge_agent.db import Project

def test_create_group_and_reject_duplicate(administrator):
    response=administrator.post('/api/admin/groups',json={'name':' 新团队 '})
    assert response.status_code==201
    assert response.json()['name']=='新团队' and response.json()['user_ids']==[]
    assert administrator.post('/api/admin/groups',json={'name':'新团队'}).json()['error_code']=='GROUP_NAME_EXISTS'
    assert administrator.post('/api/admin/groups',json={'name':'   '}).status_code==422

def test_delete_revokes_group_grants_preserves_users(administrator):
    with transaction() as db:
        before=epoch(db)
        users=db.scalar(select(func.count()).select_from(User))
        assert can_read(db,identity(db,'u1'),'project',db.get(Project,'p1'))
    assert administrator.delete('/api/admin/groups/g1').status_code==200
    with transaction() as db:
        assert db.get(Group,'g1') is None
        assert db.scalar(select(func.count()).select_from(User))==users
        assert not db.scalar(select(Membership.user_id).where(Membership.group_id=='g1'))
        assert not db.scalar(select(ACL.subject_id).where(ACL.subject_type=='group',ACL.subject_id=='g1'))
        assert epoch(db)==before+1
        assert not can_read(db,identity(db,'u1'),'project',db.get(Project,'p1'))
    assert administrator.delete('/api/admin/groups/g1').status_code==404

def test_employee_cannot_create_or_delete_groups(employee):
    assert employee.post('/api/admin/groups',json={'name':'new'}).status_code==403
    assert employee.delete('/api/admin/groups/g1').status_code==403

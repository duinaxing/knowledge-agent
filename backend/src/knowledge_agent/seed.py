"""Idempotent synthetic fixture; never import private company data."""
import argparse
from datetime import datetime, timezone
from sqlalchemy import select
from .db import transaction, User, Group, Membership, Project, Document, ACL
from .config import settings
from .security import password_hash
from .documents import upload
from .synthetic_content import content

NAMES = ['北辰', '北辰', '星河', '远航', '云桥', '青禾']
TITLES = ['项目说明','验收标准','启动会议纪要','需求变更决策','测试计划','交付清单','风险登记','角色说明','上线规范','组织知识制度']


def seed():
    password = settings.seed_password.get_secret_value()
    if len(password) < 6:
        raise RuntimeError('Set SEED_PASSWORD with at least 6 characters')
    with transaction() as db:
        if db.scalar(select(User.id).limit(1)):
            return False
        for i, name in enumerate(['研发部','交付部','产品部']):
            db.add(Group(id=f'g{i+1}', name=name))
        db.flush()
        for i in range(1, 8):
            db.add(User(id=f'u{i}', username='admin' if i == 7 else f'employee{i}',
                display_name='知识管理员' if i == 7 else f'员工{i}', role='admin' if i == 7 else 'employee',
                password_hash=password_hash(password)))
        db.flush()
        for i in range(1, 7):
            db.add(Membership(user_id=f'u{i}', group_id=f'g{(i-1)//2+1}'))
        # Employee 2 belongs to two departments to demonstrate authorized ambiguity.
        db.add(Membership(user_id='u2', group_id='g2'))
        for i, name in enumerate(NAMES, 1):
            p = Project(id=f'p{i}', code=f'PRJ-{i:03}', name=name, aliases=[name+'项目'],
                phase='testing' if i == 1 else 'development', health='delayed' if i == 1 else 'on_track',
                baseline_due_date='2026-09-20', planned_due_date='2026-09-27' if i == 1 else '2026-09-20',
                people=[{'display_name': f'负责人{i}', 'role': 'owner'}, {'display_name': f'测试员{i}', 'role': 'qa'}],
                blockers=['外部接口联调待完成'] if i == 1 else [], updated_at=datetime(2026,9,11,tzinfo=timezone.utc).timestamp())
            db.add(p)
            db.flush()
            db.add(ACL(resource_type='project', resource_id=p.id, subject_type='group', subject_id=f'g{(i-1)%3+1}'))
            for j, title in enumerate(TITLES, 1):
                d = Document(id=f'd{i:02}{j:02}', project_id=None if j==10 else p.id, title=f'{p.code} {name}·{title}',
                             type='policy' if j==10 else 'meeting' if '纪要' in title else 'project', org_visible=True)
                db.add(d)
                db.flush()
                text = content(i,j,title,p.code,name)
                upload(db, d.id, 'v1', 'document.md', text.encode())
        return True


if __name__ == '__main__':
    print('Synthetic data created; process and publish document versions.' if seed() else 'Seed already exists; unchanged.')

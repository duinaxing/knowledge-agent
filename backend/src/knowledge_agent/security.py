import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from sqlalchemy import select, update, exists, and_, or_
from .db import User, AuthSession, Membership, ACL, Project, Document, SystemState, Audit


class AppError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def password_hash(password):
    salt = secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + value


def password_ok(password, stored):
    salt, value = stored.split(':')
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return hmac.compare_digest(candidate, value)


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Identity:
    id: str
    role: str
    groups: frozenset


def identity(db, user_id):
    user = db.get(User, user_id)
    if not user or user.disabled:
        raise AppError('UNAUTHENTICATED', 401)
    groups = frozenset(db.scalars(select(Membership.group_id).where(Membership.user_id == user.id)))
    return Identity(user.id, user.role, groups)


def session_identity(db, token):
    session = db.get(AuthSession, token_hash(token or ''))
    if not session or session.expires <= time.time():
        raise AppError('UNAUTHENTICATED', 401)
    return identity(db, session.user_id), session


def epoch(db):
    return db.scalar(select(SystemState.epoch).where(SystemState.id == 1))


def check_epoch(db, expected):
    if epoch(db) != expected:
        raise AppError('CONTEXT_CHANGED', 409)


def bump_epoch(db):
    db.execute(update(SystemState).where(SystemState.id == 1).values(epoch=SystemState.epoch + 1))


def can_read(db, who, kind, resource):
    if resource is None or resource.deleted_at is not None:
        return False
    if kind == 'document' and resource.project_id:
        if not can_read(db, who, 'project', db.get(Project, resource.project_id)):
            return False
    if who.role == 'admin' or resource.org_visible:
        return True
    entries = db.scalars(select(ACL).where(ACL.resource_type == kind, ACL.resource_id == resource.id))
    return any((e.subject_type == 'user' and e.subject_id == who.id) or
               (e.subject_type == 'group' and e.subject_id in who.groups) for e in entries)


def read_filter(who, kind, model):
    """SQL equivalent of can_read, including the document/project intersection."""
    grant=exists(select(ACL.resource_id).where(ACL.resource_type==kind,ACL.resource_id==model.id,
        or_(and_(ACL.subject_type=='user',ACL.subject_id==who.id),
            and_(ACL.subject_type=='group',ACL.subject_id.in_(who.groups)))))
    own=True if who.role=='admin' else or_(model.org_visible.is_(True),grant)
    if kind=='document':
        project=exists(select(Project.id).where(Project.id==model.project_id,read_filter(who,'project',Project)))
        return and_(model.deleted_at.is_(None),own,or_(model.project_id.is_(None),project))
    return and_(model.deleted_at.is_(None),own)


def require_read(db, who, kind, resource_id):
    resource = db.get(Project if kind == 'project' else Document, resource_id)
    if not can_read(db, who, kind, resource):
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    return resource


def audit(db, who, action, resource_id):
    db.add(Audit(actor_id=who.id, action=action, resource_id=resource_id))

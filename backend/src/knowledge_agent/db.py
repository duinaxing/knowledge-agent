"""PostgreSQL in deployment; SQLite is an explicit engineering-test backend."""
import time
import uuid
from contextlib import contextmanager
from sqlalchemy import (create_engine, Column, String, Integer, Float, Boolean, JSON,
                        ForeignKey, UniqueConstraint, Index, text, event)
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from pgvector.sqlalchemy import Vector
from .config import settings


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id = Column(String, primary_key=True, default=uid)
    username = Column(String(80), unique=True, nullable=False)
    display_name = Column(String(100), nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default='employee')
    disabled = Column(Boolean, nullable=False, default=False)


class Group(Base):
    __tablename__ = 'groups'
    id = Column(String, primary_key=True, default=uid)
    name = Column(String, unique=True, nullable=False)


class Membership(Base):
    __tablename__ = 'user_groups'
    user_id = Column(String, ForeignKey('users.id'), primary_key=True)
    group_id = Column(String, ForeignKey('groups.id'), primary_key=True)


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('users.id'), nullable=False)
    csrf = Column(String, nullable=False)
    expires = Column(Float, nullable=False)


class SystemState(Base):
    __tablename__ = 'system_state'
    id = Column(Integer, primary_key=True)
    epoch = Column(Integer, nullable=False, default=1)


class Project(Base):
    __tablename__ = 'projects'
    id = Column(String, primary_key=True, default=uid)
    code = Column(String(40), nullable=False)
    name = Column(String(100), nullable=False)
    aliases = Column(JSON, nullable=False, default=list)
    summary = Column(String(4000), nullable=False, default='', server_default='')
    deleted_at = Column(Float, nullable=True)
    __table_args__ = (Index('project_active_code', 'code', unique=True,
        postgresql_where=text('deleted_at IS NULL'), sqlite_where=text('deleted_at IS NULL')),)
    phase = Column(String, nullable=False, default='planning')
    health = Column(String, nullable=False, default='unknown')
    baseline_due_date = Column(String, nullable=True)
    planned_due_date = Column(String, nullable=True)
    actual_delivery_date = Column(String, nullable=True)
    blockers = Column(JSON, nullable=False, default=list)
    people = Column(JSON, nullable=False, default=list)
    org_visible = Column(Boolean, nullable=False, default=False)
    updated_at = Column(Float, nullable=False, default=time.time)
    revision = Column(Integer, nullable=False, default=1)


class ACL(Base):
    __tablename__ = 'acl_entries'
    resource_type = Column(String, primary_key=True)
    resource_id = Column(String, primary_key=True)
    subject_type = Column(String, primary_key=True)
    subject_id = Column(String, primary_key=True)


class Document(Base):
    __tablename__ = 'documents'
    id = Column(String, primary_key=True, default=uid)
    project_id = Column(String, ForeignKey('projects.id'), nullable=True)
    title = Column(String(200), nullable=False)
    type = Column(String, nullable=False, default='project')
    org_visible = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(Float, nullable=True)
    revision = Column(Integer, nullable=False, default=1)


class Version(Base):
    __tablename__ = 'document_versions'
    id = Column(String, primary_key=True, default=uid)
    document_id = Column(String, ForeignKey('documents.id'), nullable=False)
    version_label = Column(String(80), nullable=False)
    content_hash = Column(String, nullable=False)
    path = Column(String, nullable=False)
    suffix = Column(String, nullable=False)
    processing = Column(String, nullable=False, default='uploaded')
    publication = Column(String, nullable=False, default='draft')
    effective_from = Column(Float, nullable=True)
    effective_to = Column(Float, nullable=True)
    embedding_model = Column(String, nullable=True)
    embedding_dim = Column(Integer, nullable=True)
    error_code = Column(String, nullable=True)
    __table_args__ = (UniqueConstraint('document_id', 'version_label'),)


class Chunk(Base):
    __tablename__ = 'chunks'
    id = Column(String, primary_key=True, default=uid)
    version_id = Column(String, ForeignKey('document_versions.id'), nullable=False)
    ordinal = Column(Integer, nullable=False)
    text = Column(String, nullable=False)
    locator = Column(JSON, nullable=False)
    embedding = Column(Vector().with_variant(JSON(), 'sqlite'), nullable=False)
    __table_args__ = (UniqueConstraint('version_id', 'ordinal'),)


class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(String, primary_key=True, default=uid)
    owner_id = Column(String, ForeignKey('users.id'), nullable=False)
    epoch = Column(Integer, nullable=False)
    project_id = Column(String, nullable=True)
    candidates = Column(JSON, nullable=False, default=list)
    created_at = Column(Float, nullable=False, default=time.time)
    title = Column(String(100), nullable=False, default='新会话', server_default='新会话')
    deleted_at = Column(Float, nullable=True)
    __table_args__ = (Index('conversation_owner_created', 'owner_id', 'created_at', 'id'),)


class Run(Base):
    __tablename__ = 'runs'
    id = Column(String, primary_key=True, default=uid)
    conversation_id = Column(String, ForeignKey('conversations.id'), nullable=False)
    owner_id = Column(String, ForeignKey('users.id'), nullable=False)
    client_request_id = Column(String(100), nullable=False)
    message = Column(String, nullable=False)
    epoch = Column(Integer, nullable=False)
    state = Column(String, nullable=False, default='queued')
    answer = Column(JSON, nullable=True)
    deadline = Column(Float, nullable=False)
    created_at = Column(Float, nullable=False, default=time.time)
    tool_count = Column(Integer, nullable=False, default=0)
    model_count = Column(Integer, nullable=False, default=0)
    signatures = Column(JSON, nullable=False, default=dict)
    usage = Column(JSON, nullable=False, default=list)
    lease_until = Column(Float, nullable=True)
    generation = Column(Integer, nullable=False, default=0)
    error_code = Column(String, nullable=True)
    __table_args__ = (
        UniqueConstraint('owner_id', 'conversation_id', 'client_request_id'),
        Index('one_active_run', 'conversation_id', unique=True,
              postgresql_where=text("state IN ('queued','running','interrupted')"),
              sqlite_where=text("state IN ('queued','running','interrupted')")),
    )


class RunEvent(Base):
    __tablename__ = 'run_events'
    run_id = Column(String, ForeignKey('runs.id'), primary_key=True)
    sequence = Column(Integer, primary_key=True)
    kind = Column(String, nullable=False)
    data = Column(JSON, nullable=False, default=dict)


class Job(Base):
    __tablename__ = 'jobs'
    id = Column(String, primary_key=True, default=uid)
    kind = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    state = Column(String, nullable=False, default='queued')
    attempts = Column(Integer, nullable=False, default=0)
    generation = Column(Integer, nullable=False, default=0)
    lease_until = Column(Float, nullable=True)
    error_code = Column(String, nullable=True)
    created_at = Column(Float, nullable=False, default=time.time)


class Feedback(Base):
    __tablename__ = 'feedback'
    id = Column(String, primary_key=True, default=uid)
    run_id = Column(String, ForeignKey('runs.id'), nullable=False)
    owner_id = Column(String, ForeignKey('users.id'), nullable=False)
    type = Column(String, nullable=False)
    note = Column(String, nullable=False)
    state = Column(String, nullable=False, default='open')
    created_at = Column(Float, nullable=False, default=time.time)


class Audit(Base):
    __tablename__ = 'audit_events'
    id = Column(String, primary_key=True, default=uid)
    actor_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    resource_id = Column(String, nullable=False)
    created_at = Column(Float, nullable=False, default=time.time)


class RateLimit(Base):
    __tablename__ = 'rate_limits'
    key = Column(String, primary_key=True)
    count = Column(Integer, nullable=False)
    expires = Column(Float, nullable=False)


Index('run_conversation_created', Run.conversation_id, Run.created_at, Run.id)
Index('run_queue', Run.state, Run.created_at)
Index('job_queue', Job.state, Job.created_at)
Index('version_document_state', Version.document_id, Version.processing, Version.publication)
Index('session_user', AuthSession.user_id)


engine = create_engine(settings.database_url, pool_pre_ping=True,
    connect_args={'connect_timeout': 5, 'options': '-c statement_timeout=10000 -c lock_timeout=5000'}
    if settings.database_url.startswith('postgresql') else {})
if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def sqlite_fk(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')
Session = sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction():
    with Session() as db:
        try:
            with db.begin():
                yield db
        except BaseException:
            # Only generated files created in this transaction are registered here.
            for path in db.info.get('uploaded_files',[]):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise


def initialize_test_database():
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('Use Alembic for PostgreSQL')
    Base.metadata.create_all(engine)
    with transaction() as db:
        if not db.get(SystemState, 1):
            db.add(SystemState(id=1, epoch=1))

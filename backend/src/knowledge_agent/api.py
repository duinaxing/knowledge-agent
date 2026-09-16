import asyncio
import json
import secrets
import time
import logging
import uuid
from collections import defaultdict, deque
from typing import Annotated
from fastapi import FastAPI, Request, Response, Depends, UploadFile, File, Form, Query as QueryParam
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select, delete, text, func
from sqlalchemy.exc import IntegrityError
from .config import settings
from .db import (transaction, User, Group, Membership, AuthSession, Project, Document, Version,
                 ACL, Run, RunEvent, Conversation, Job, Feedback, Chunk)
from .security import (AppError, session_identity, identity, password_ok, token_hash, epoch,
                       bump_epoch, require_read, can_read, audit, check_epoch)
from .schemas import (Login, Query, ProjectWrite, ACLWrite, Members, Publish, FeedbackWrite, FeedbackState)
from .runs import owned_run, owned_conversation, submit
from .documents import upload, publish, delete_document, excerpt
from .retrieval import visible_projects, project_evidence
from .schemas import Register, ChangePassword, RenameConversation, GroupCreate, DocumentSelection
from .security import password_hash
from .accounts import throttle
from .history import readable_answer

app = FastAPI(title='Project Knowledge Agent Lite', version='0.1.0')
from .request_limits import RequestLimits
app.add_middleware(RequestLimits)
login_attempts = defaultdict(deque)


@app.exception_handler(AppError)
async def app_error(request, exc):
    if exc.code in ('NOT_FOUND_OR_FORBIDDEN', 'ADMIN_REQUIRED', 'CSRF_REJECTED'):
        from .db import Audit
        with transaction() as db:
            try:
                who, _ = session_identity(db, request.cookies.get('session'))
                actor = who.id
            except AppError:
                actor = 'anonymous'
            db.add(Audit(actor_id=actor, action='access.denied.' + exc.code,
                         resource_id=request.state.request_id))
    return JSONResponse({'error_code': exc.code}, status_code=exc.status)


@app.exception_handler(IntegrityError)
async def integrity_error(_, exc):
    if getattr(getattr(exc.orig,'diag',None),'constraint_name',None)=='project_active_code':
        return JSONResponse({'error_code':'PROJECT_CODE_EXISTS'},status_code=409)
    return JSONResponse({'error_code': 'CONFLICT'}, status_code=409)


@app.middleware('http')
async def headers(request, call_next):
    started = time.monotonic()
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers['X-Request-ID'] = request.state.request_id
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    logging.getLogger('knowledge.requests').info('request_id=%s status=%s seconds=%.3f',
        request.state.request_id, response.status_code, time.monotonic() - started)
    return response


def current(request: Request):
    with transaction() as db:
        who, session = session_identity(db, request.cookies.get('session'))
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            if request.headers.get('origin') not in settings.origins or not secrets.compare_digest(
                    request.headers.get('x-csrf-token', '').encode('utf-8'), session.csrf.encode('utf-8')):
                raise AppError('CSRF_REJECTED', 403)
        return who


def admin(who=Depends(current)):
    if who.role != 'admin':
        raise AppError('ADMIN_REQUIRED', 403)
    return who


@app.post('/api/auth/register', status_code=201)
def register(body: Register, request: Request):
    if request.headers.get('origin') not in settings.origins:
        raise AppError('CSRF_REJECTED',403)
    throttle(request.client.host if request.client else 'unknown','register',10)
    with transaction() as db:
        if body.username=='admin' or db.scalar(select(User.id).where(User.username==body.username)):
            raise AppError('USERNAME_TAKEN',409)
        user=User(username=body.username,display_name=body.display_name.strip() or body.username,
                  password_hash=password_hash(body.password),role='employee')
        db.add(user);db.flush()
        return {'id':user.id,'username':user.username}


@app.post('/api/auth/password')
def change_password(body: ChangePassword, response: Response, who=Depends(current)):
    throttle(who.id,'password',10)
    with transaction() as db:
        user=db.scalar(select(User).where(User.id==who.id).with_for_update())
        if not password_ok(body.current_password,user.password_hash):
            raise AppError('INVALID_CREDENTIALS',400)
        user.password_hash=password_hash(body.new_password)
        db.execute(delete(AuthSession).where(AuthSession.user_id==who.id))
        audit(db,who,'password.change',who.id)
    response.delete_cookie('session')
    return {'ok':True}


def project_json(p):
    return {key: getattr(p, key) for key in ('id','code','name','aliases','phase','health',
        'baseline_due_date','planned_due_date','actual_delivery_date','blockers','people','org_visible','revision','updated_at','summary')}


def version_json(v):
    return {k: getattr(v, k) for k in ('id','document_id','version_label','processing','publication',
                                     'effective_from','effective_to','error_code','embedding_model','embedding_dim')}


def run_json(run):
    return {k: getattr(run, k) for k in ('id','state','answer','error_code','tool_count','model_count','usage','created_at','deadline')}


@app.get('/api/health')
def health():
    return {'status': 'ok'}


@app.get('/api/ready')
def ready():
    with transaction() as db:
        db.execute(text('SELECT 1'))
        e = epoch(db)
    return {'status': 'ready', 'epoch': e, 'model_configured': bool(settings.model_api_key.get_secret_value()),
            'embedding_configured': bool(settings.embedding_model), 'mode': settings.model_mode}


@app.post('/api/auth/login')
def login(body: Login, request: Request, response: Response):
    if request.headers.get('origin') not in settings.origins:
        raise AppError('CSRF_REJECTED', 403)
    address = request.client.host if request.client else 'unknown'
    throttle(address,'login',30)
    now = time.time()
    with transaction() as db:
        user = db.scalar(select(User).where(User.username == body.username).with_for_update())
        if not user or user.disabled or not password_ok(body.password, user.password_hash):
            raise AppError('INVALID_CREDENTIALS', 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.add(AuthSession(id=token_hash(token), user_id=user.id, csrf=csrf, expires=now + 8*3600))
        response.set_cookie('session', token, httponly=True, samesite='strict', secure=settings.cookie_secure, max_age=8*3600)
        return {'id': user.id, 'username': user.username, 'display_name': user.display_name, 'role': user.role, 'csrf': csrf}


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, who=Depends(current)):
    with transaction() as db:
        db.execute(delete(AuthSession).where(AuthSession.id == token_hash(request.cookies['session'])))
    response.delete_cookie('session')
    return {'ok': True}


@app.get('/api/me')
def me(request: Request, who=Depends(current)):
    with transaction() as db:
        user = db.get(User, who.id)
        _, session = session_identity(db, request.cookies.get('session'))
        return {'id': user.id, 'username': user.username, 'display_name': user.display_name,
                'role': user.role, 'csrf': session.csrf, 'epoch': epoch(db)}


@app.get('/api/projects')
def projects(who=Depends(current)):
    with transaction() as db:
        expected=epoch(db)
        who=identity(db,who.id)
        result=[project_json(p) for p in visible_projects(db, who)]
        check_epoch(db,expected)
        return result


@app.get('/api/conversations')
def conversations(offset: int = QueryParam(0,ge=0), limit: int = QueryParam(50,ge=1,le=100), who=Depends(current)):
    with transaction() as db:
        current_epoch = epoch(db)
        return [{'id': c.id, 'title':c.title, 'created_at': c.created_at, 'expired': c.epoch != current_epoch}
                for c in db.scalars(select(Conversation).where(Conversation.owner_id == who.id,Conversation.deleted_at.is_(None))
                                   .order_by(Conversation.created_at.desc(),Conversation.id.desc()).offset(offset).limit(limit))]


@app.patch('/api/conversations/{id_}')
def rename_conversation(id_: str, body: RenameConversation, who=Depends(current)):
    with transaction() as db:
        conv=owned_conversation(db,who,id_,fresh=False)
        conv.title=body.title.strip() or '新会话'
    return {'ok':True}


@app.delete('/api/conversations/{id_}')
def delete_conversation(id_: str, who=Depends(current)):
    with transaction() as db:
        conv=db.scalar(select(Conversation).where(Conversation.id==id_).with_for_update())
        owned_conversation(db,who,id_,fresh=False)
        conv.deleted_at=time.time()
        for run in db.scalars(select(Run).where(Run.conversation_id==id_).with_for_update()):
            run.generation+=1
            run.state,run.error_code='failed','CONVERSATION_DELETED'
            run.message,run.answer='[已删除]',None
            db.execute(delete(RunEvent).where(RunEvent.run_id==run.id))
            for feedback in db.scalars(select(Feedback).where(Feedback.run_id==run.id)):
                feedback.note='[会话已删除]'
        audit(db,who,'conversation.delete',id_)
    return {'ok':True}


@app.post('/api/conversations', status_code=201)
def create_conversation(who=Depends(current)):
    with transaction() as db:
        conv = Conversation(owner_id=who.id, epoch=epoch(db))
        db.add(conv)
        db.flush()
        return {'id': conv.id, 'epoch': conv.epoch}


@app.get('/api/conversations/{id_}/messages')
def messages(id_: str, offset: int = QueryParam(0,ge=0), limit: int = QueryParam(50,ge=1,le=200), who=Depends(current)):
    with transaction() as db:
        expected=epoch(db)
        who=identity(db,who.id)
        conv = owned_conversation(db, who, id_, fresh=False)
        rows=list(db.scalars(select(Run).where(Run.conversation_id==conv.id)
                  .order_by(Run.created_at.desc(),Run.id.desc()).offset(offset).limit(limit+1)))
        result={'expired': False, 'knowledge_updated':conv.epoch!=expected,'has_more':len(rows)>limit,
                'messages':[dict(run_json(r),message=r.message,answer=readable_answer(db,who,r.answer,r.epoch!=expected)) for r in reversed(rows[:limit])]}
        check_epoch(db,expected)
        return result


@app.post('/api/conversations/{id_}/queries', status_code=202)
def queries(id_: str, body: Query, who=Depends(current)):
    throttle(who.id,'query',20)
    with transaction() as db:
        run = submit(db, who, id_, body)
        return {'run_id': run.id}


@app.get('/api/runs/{id_}')
def get_run(id_: str, who=Depends(current)):
    with transaction() as db:
        return run_json(owned_run(db, who, id_))


@app.post('/api/runs/{id_}/resume', status_code=202)
def resume(id_: str, who=Depends(current)):
    with transaction() as db:
        run = db.scalar(select(Run).where(Run.id == id_).with_for_update())
        owned_run(db, who, id_)
        if run.state != 'interrupted':
            raise AppError('NOT_INTERRUPTED', 409)
        if run.deadline <= time.time() or run.tool_count >= settings.max_tools or run.model_count >= settings.max_models:
            raise AppError('RECOVERY_EXPIRED', 409)
        run.state, run.error_code = 'queued', None
        return {'run_id': run.id}


@app.get('/api/runs/{id_}/events')
async def events(id_: str, request: Request, who=Depends(current)):
    try:
        cursor = int(request.headers.get('last-event-id', '0'))
    except ValueError:
        raise AppError('INVALID_EVENT_ID')
    if cursor < 0:
        raise AppError('INVALID_EVENT_ID')
    with transaction() as db:
        owned_run(db, who, id_)
    async def stream():
        nonlocal cursor
        while not await request.is_disconnected():
            try:
                def snapshot():
                    with transaction() as db:
                        fresh, _ = session_identity(db, request.cookies.get('session'))
                        run = owned_run(db, fresh, id_)
                        batch = list(db.scalars(select(RunEvent).where(RunEvent.run_id == id_, RunEvent.sequence > cursor)
                                                .order_by(RunEvent.sequence).limit(100)))
                        return batch, run.state
                batch, state = await asyncio.to_thread(snapshot)
                for e in batch:
                    # No answer text stored in events; final is fetched via authenticated run API.
                    cursor = e.sequence
                    yield f'id: {cursor}\nevent: {e.kind}\ndata: {json.dumps(dict(e.data, run_id=id_), ensure_ascii=False)}\n\n'
                if state in ('completed', 'failed', 'interrupted'):
                    yield f'event: done\ndata: {json.dumps({"state": state})}\n\n'
                    break
                yield ': heartbeat\n\n'
                await asyncio.sleep(.5)
            except AppError as exc:
                yield f'event: error\ndata: {json.dumps({"error_code": exc.code})}\n\n'
                break
    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


@app.get('/api/documents/{id_}/versions/{version_id}/chunks/{chunk_id}')
def get_excerpt(id_: str, version_id: str, chunk_id: str, who=Depends(current)):
    with transaction() as db:
        expected=epoch(db)
        result=excerpt(db,identity(db,who.id),id_,version_id,chunk_id)
        check_epoch(db,expected)
        return result


@app.post('/api/answers/{id_}/feedback', status_code=201)
def feedback(id_: str, body: FeedbackWrite, who=Depends(current)):
    with transaction() as db:
        run = owned_run(db, who, id_)
        if not run.answer:
            raise AppError('ANSWER_NOT_AVAILABLE', 409)
        row = Feedback(run_id=run.id, owner_id=who.id, **body.model_dump())
        db.add(row)
        db.flush()
        return {'id': row.id}


@app.get('/api/admin/projects')
def admin_projects(who=Depends(admin)):
    return projects(who)


@app.post('/api/admin/projects', status_code=201)
def new_project(body: ProjectWrite, who=Depends(admin)):
    with transaction() as db:
        if db.scalar(select(Project.id).where(Project.code==body.code,Project.deleted_at.is_(None))):
            raise AppError('PROJECT_CODE_EXISTS',409)
        row = Project(**body.model_dump(mode='json', exclude={'revision'}))
        db.add(row)
        db.flush()
        audit(db, who, 'project.create', row.id)
        return project_json(row)


@app.patch('/api/admin/projects/{id_}')
def edit_project(id_: str, body: ProjectWrite, who=Depends(admin)):
    with transaction() as db:
        row = db.scalar(select(Project).where(Project.id == id_).with_for_update())
        if not row or row.deleted_at is not None:
            raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
        if body.revision != row.revision:
            raise AppError('REVISION_CONFLICT', 409)
        if db.scalar(select(Project.id).where(Project.code==body.code,Project.id!=id_,Project.deleted_at.is_(None))):
            raise AppError('PROJECT_CODE_EXISTS',409)
        if body.org_visible != row.org_visible:
            bump_epoch(db)
        for key, value in body.model_dump(mode='json', exclude={'revision'}).items():
            setattr(row, key, value)
        row.revision += 1
        row.updated_at = time.time()
        audit(db, who, 'project.update', row.id)
        return project_json(row)


@app.delete('/api/admin/projects/{id_}')
def delete_project(id_: str, who=Depends(admin)):
    with transaction() as db:
        row=db.scalar(select(Project).where(Project.id==id_).with_for_update())
        require_read(db,who,'project',id_)
        row.deleted_at=time.time();row.revision+=1
        bump_epoch(db)
        audit(db,who,'project.delete',id_)
    return {'ok':True}


@app.get('/api/admin/documents')
def admin_documents(offset: int = QueryParam(0, ge=0), limit: int = QueryParam(100, ge=1, le=100), who=Depends(admin)):
    with transaction() as db:
        docs = list(db.scalars(select(Document).where(Document.deleted_at.is_(None)).order_by(Document.title, Document.id).offset(offset).limit(limit)))
        version_groups = defaultdict(list)
        for v in db.scalars(select(Version).where(Version.document_id.in_([d.id for d in docs])).order_by(Version.version_label)):
            version_groups[v.document_id].append(version_json(v))
        return [dict(id=d.id, title=d.title, project_id=d.project_id, type=d.type, org_visible=d.org_visible,
                     revision=d.revision, deleted_at=d.deleted_at, versions=version_groups[d.id]) for d in docs]


@app.post('/api/admin/documents', status_code=201)
async def new_document(file: Annotated[UploadFile, File()], title: Annotated[str, Form(max_length=200)],
                       version_label: Annotated[str, Form(max_length=80)] = 'v1',
                       project_id: Annotated[str | None, Form()] = None,
                       type: Annotated[str, Form(max_length=40)] = 'project', who=Depends(admin)):
    body = await file.read(settings.max_upload_bytes + 1)
    with transaction() as db:
        if project_id:
            require_read(db, who, 'project', project_id)
        doc = Document(title=title, project_id=project_id or None, type=type)
        db.add(doc)
        db.flush()
        version = upload(db, doc.id, version_label, file.filename, body)
        audit(db, who, 'document.upload', doc.id)
        return {'id': doc.id, 'version_id': version.id}


@app.get('/api/admin/documents/{id_}/versions/{version_id}/content')
def document_content(id_: str, version_id: str, offset: int = QueryParam(0,ge=0),
                     limit: int = QueryParam(50,ge=1,le=100), who=Depends(admin)):
    with transaction() as db:
        doc=db.get(Document,id_)
        if not doc or doc.deleted_at is not None:
            raise AppError('NOT_FOUND_OR_FORBIDDEN',404)
        version=db.get(Version,version_id)
        if not version or version.document_id!=doc.id:
            raise AppError('NOT_FOUND_OR_FORBIDDEN',404)
        rows=list(db.scalars(select(Chunk).where(Chunk.version_id==version.id)
                  .order_by(Chunk.ordinal).offset(offset).limit(limit+1)))
        return {'document_id':doc.id,'title':doc.title,'version':version_json(version),
                'chunks':[{'id':c.id,'ordinal':c.ordinal,'text':c.text,'locator':c.locator} for c in rows[:limit]],
                'has_more':len(rows)>limit}


@app.post('/api/admin/documents/{id_}/versions', status_code=201)
async def new_version(id_: str, file: Annotated[UploadFile, File()],
                      version_label: Annotated[str, Form(max_length=80)], who=Depends(admin)):
    body = await file.read(settings.max_upload_bytes + 1)
    with transaction() as db:
        v = upload(db, id_, version_label, file.filename, body)
        audit(db, who, 'version.upload', id_)
        return {'version_id': v.id}


@app.post('/api/admin/versions/{id_}/publish')
def publish_version(id_: str, body: Publish, who=Depends(admin)):
    with transaction() as db:
        publish(db, id_, body)
        audit(db, who, 'version.publish', id_)
    return {'ok': True}


@app.post('/api/admin/versions/{id_}/retry')
def retry_version(id_: str, who=Depends(admin)):
    with transaction() as db:
        v = db.scalar(select(Version).where(Version.id == id_).with_for_update())
        if not v or v.processing != 'failed' or db.get(Document, v.document_id).deleted_at:
            raise AppError('NOT_RETRYABLE', 409)
        v.processing, v.error_code = 'uploaded', None
        db.add(Job(kind='index', target_id=v.id))
        audit(db, who, 'version.retry', id_)
    return {'ok': True}


@app.delete('/api/admin/documents/{id_}')
def remove_document(id_: str, who=Depends(admin)):
    with transaction() as db:
        delete_document(db, id_)
        audit(db, who, 'document.delete', id_)
    return {'ok': True}


@app.post('/api/admin/documents/bulk-delete')
def bulk_delete_documents(body: DocumentSelection, who=Depends(admin)):
    ids = sorted(set(body.document_ids))
    with transaction() as db:
        rows = list(db.scalars(select(Document).where(Document.id.in_(ids)).order_by(Document.id).with_for_update()))
        if len(rows) != len(ids):
            raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
        for id_ in ids:
            delete_document(db, id_)
            audit(db, who, 'document.delete', id_)
    return {'ok': True, 'document_ids': ids}


@app.get('/api/admin/resources/{kind}/{id_}/acl')
def get_acl(kind: str, id_: str, who=Depends(admin)):
    if kind not in ('project','document'):
        raise AppError('INVALID_ARGUMENT')
    with transaction() as db:
        row = require_read(db, who, kind, id_)
        return {'org_visible': row.org_visible, 'grants': [{'subject_type': g.subject_type, 'subject_id': g.subject_id}
                for g in db.scalars(select(ACL).where(ACL.resource_type == kind, ACL.resource_id == id_))]}


@app.put('/api/admin/resources/{kind}/{id_}/acl')
def set_acl(kind: str, id_: str, body: ACLWrite, who=Depends(admin)):
    if kind not in ('project','document'):
        raise AppError('INVALID_ARGUMENT')
    with transaction() as db:
        row = require_read(db, who, kind, id_)
        for grant in sorted(body.grants,key=lambda g:(g.subject_type,g.subject_id)):
            model=User if grant.subject_type=='user' else Group
            if not db.scalar(select(model).where(model.id==grant.subject_id).with_for_update()):
                raise AppError('INVALID_SUBJECT')
        row.org_visible = body.org_visible
        db.execute(delete(ACL).where(ACL.resource_type == kind, ACL.resource_id == id_))
        for kind_, subject_id in {(g.subject_type, g.subject_id) for g in body.grants}:
            db.add(ACL(resource_type=kind, resource_id=id_, subject_type=kind_, subject_id=subject_id))
        bump_epoch(db)
        audit(db, who, 'acl.update', id_)
    return {'ok': True}


@app.get('/api/admin/users')
def users(who=Depends(admin)):
    with transaction() as db:
        return [{'id': u.id, 'username': u.username, 'display_name': u.display_name, 'role': u.role}
                for u in db.scalars(select(User))]


@app.get('/api/admin/groups')
def groups(who=Depends(admin)):
    with transaction() as db:
        return [{'id': g.id, 'name': g.name, 'user_ids': list(db.scalars(select(Membership.user_id).where(Membership.group_id == g.id)))}
                for g in db.scalars(select(Group))]


@app.post('/api/admin/groups',status_code=201)
def create_group(body: GroupCreate, who=Depends(admin)):
    with transaction() as db:
        if db.scalar(select(Group.id).where(Group.name==body.name)):
            raise AppError('GROUP_NAME_EXISTS',409)
        row=Group(name=body.name)
        db.add(row);db.flush()
        audit(db,who,'group.create',row.id)
        return {'id':row.id,'name':row.name,'user_ids':[]}


@app.delete('/api/admin/groups/{id_}')
def delete_group(id_: str, who=Depends(admin)):
    with transaction() as db:
        row=db.scalar(select(Group).where(Group.id==id_).with_for_update())
        if not row:
            raise AppError('NOT_FOUND_OR_FORBIDDEN',404)
        db.execute(delete(Membership).where(Membership.group_id==id_))
        db.execute(delete(ACL).where(ACL.subject_type=='group',ACL.subject_id==id_))
        db.delete(row)
        bump_epoch(db)
        audit(db,who,'group.delete',id_)
    return {'ok':True}


@app.put('/api/admin/groups/{id_}/members')
def group_members(id_: str, body: Members, who=Depends(admin)):
    with transaction() as db:
        if not db.scalar(select(Group).where(Group.id==id_).with_for_update()) or any(not db.get(User, u) for u in body.user_ids):
            raise AppError('INVALID_SUBJECT')
        db.execute(delete(Membership).where(Membership.group_id == id_))
        for user_id in set(body.user_ids):
            db.add(Membership(group_id=id_, user_id=user_id))
        bump_epoch(db)
        audit(db, who, 'group.members.update', id_)
    return {'ok': True}


@app.put('/api/admin/groups/{id_}/members/{user_id}')
@app.delete('/api/admin/groups/{id_}/members/{user_id}')
def change_group_member(id_: str, user_id: str, request: Request, who=Depends(admin)):
    with transaction() as db:
        if not db.scalar(select(Group).where(Group.id == id_).with_for_update()) or not db.get(User, user_id):
            raise AppError('INVALID_SUBJECT')
        member = db.scalar(select(Membership).where(Membership.group_id == id_, Membership.user_id == user_id))
        if request.method == 'PUT' and not member:
            db.add(Membership(group_id=id_, user_id=user_id))
        elif request.method == 'DELETE' and member:
            db.delete(member)
        else:
            return {'ok': True}
        bump_epoch(db)
        audit(db, who, 'group.members.update', id_)
    return {'ok': True}


@app.get('/api/admin/jobs')
def jobs(who=Depends(admin)):
    with transaction() as db:
        return [{k: getattr(j, k) for k in ('id','kind','target_id','state','attempts','error_code','created_at')}
                for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100))]


@app.get('/api/admin/metrics')
def metrics(who=Depends(admin)):
    with transaction() as db:
        return {'runs':dict(db.execute(select(Run.state,func.count()).group_by(Run.state)).all()),
                'jobs':dict(db.execute(select(Job.state,func.count()).group_by(Job.state)).all()),
                'worker_concurrency':settings.worker_concurrency,
                'query_per_user_per_minute':20,'max_context_bytes':settings.max_context_bytes}


@app.post('/api/admin/jobs/{id_}/retry')
def retry_cleanup(id_: str, who=Depends(admin)):
    with transaction() as db:
        j = db.get(Job, id_)
        if not j or j.kind != 'cleanup' or j.state != 'failed':
            raise AppError('NOT_RETRYABLE', 409)
        j.state, j.error_code = 'queued', None
        audit(db, who, 'cleanup.retry', id_)
    return {'ok': True}


@app.get('/api/admin/feedback')
def admin_feedback(who=Depends(admin)):
    with transaction() as db:
        result = []
        for f in db.scalars(select(Feedback).order_by(Feedback.created_at.desc()).limit(100)):
            run = db.get(Run, f.run_id)
            fresh = run.epoch == epoch(db)
            # Notes can include copied restricted content, so hide them with expired answers.
            result.append({'id': f.id, 'run_id': f.run_id, 'type': f.type, 'state': f.state,
                'created_at': f.created_at, 'note': f.note if fresh else '[内容已失效]',
                'answer': run.answer if fresh else None})
        return result


@app.patch('/api/admin/feedback/{id_}')
def update_feedback(id_: str, body: FeedbackState, who=Depends(admin)):
    with transaction() as db:
        f = db.get(Feedback, id_)
        if not f:
            raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
        f.state = body.state
        audit(db, who, 'feedback.update', id_)
    return {'ok': True}

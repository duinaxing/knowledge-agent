import time
from sqlalchemy import select, func
from .db import Run, RunEvent, Conversation, transaction
from .security import AppError, check_epoch, epoch
from .config import settings


def event(db, run, kind, data=None):
    n = (db.scalar(select(func.max(RunEvent.sequence)).where(RunEvent.run_id == run.id)) or 0) + 1
    db.add(RunEvent(run_id=run.id, sequence=n, kind=kind, data=data or {}))


def owned_run(db, who, run_id, fresh=True):
    run = db.get(Run, run_id)
    if not run or run.owner_id != who.id or db.get(Conversation,run.conversation_id).deleted_at is not None:
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    if fresh:
        check_epoch(db, run.epoch)
    return run


def owned_conversation(db, who, conversation_id, fresh=True):
    conv = db.get(Conversation, conversation_id)
    if not conv or conv.owner_id != who.id or conv.deleted_at is not None:
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    if fresh:
        check_epoch(db, conv.epoch)
    return conv


def submit(db, who, conversation_id, body):
    conv = db.scalar(select(Conversation).where(Conversation.id == conversation_id).with_for_update())
    owned_conversation(db, who, conversation_id, fresh=False)
    old = db.scalar(select(Run).where(Run.conversation_id == conv.id, Run.client_request_id == body.client_request_id,
                                    Run.owner_id == who.id))
    if old:
        if old.message != body.message:
            raise AppError('IDEMPOTENCY_CONFLICT', 409)
        return old
    active = db.scalar(select(Run).where(Run.conversation_id == conv.id,
                           Run.state.in_(['queued', 'running', 'interrupted'])).with_for_update())
    if active:
        if active.deadline <= time.time():
            active.state, active.error_code = 'failed', 'DEADLINE_EXCEEDED'
            active.generation += 1
            db.flush()
        else:
            raise AppError('CONVERSATION_BUSY', 409)
    if conv.epoch != epoch(db):
        conv.epoch=epoch(db)
        conv.project_id,conv.candidates=None,[]
    if conv.title=='新会话':
        conv.title=body.message[:60]
    run = Run(conversation_id=conv.id, owner_id=who.id, client_request_id=body.client_request_id,
              message=body.message, epoch=conv.epoch, deadline=time.time() + settings.run_timeout)
    db.add(run)
    db.flush()
    event(db, run, 'queued')
    return run


def leased(db, run_id, generation):
    run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if not run or run.state != 'running' or run.generation != generation or run.lease_until <= time.time():
        raise AppError('LEASE_LOST', 409)
    check_epoch(db, run.epoch)
    if run.deadline <= time.time():
        raise AppError('DEADLINE_EXCEEDED', 408)
    return run


def reserve(run_id, generation, kind, signature=None):
    with transaction() as db:
        run = leased(db, run_id, generation)
        field, limit = ('tool_count', settings.max_tools) if kind == 'tool' else ('model_count', settings.max_models)
        if getattr(run, field) >= limit:
            raise AppError('BUDGET_EXCEEDED', 409)
        if signature:
            signatures = dict(run.signatures)
            if signatures.get('blocked:' + signature):
                raise AppError('NON_RETRYABLE_RESULT', 409)
            if signatures.get(signature, 0) >= 2:
                raise AppError('REPEATED_CALL_LIMIT', 409)
            if signature.startswith('search_documents:') and signature not in signatures:
                searches = [key for key in signatures if key.startswith('search_documents:')]
                if len(searches) >= 2:
                    raise AppError('QUERY_REWRITE_LIMIT', 409)
            signatures[signature] = signatures.get(signature, 0) + 1
            run.signatures = signatures
        setattr(run, field, getattr(run, field) + 1)
        return min(settings.tool_timeout if kind == 'tool' else settings.model_timeout,
                   max(.01, run.deadline - time.time()))


def mark_non_retryable(run_id, generation, signature):
    with transaction() as db:
        run = leased(db, run_id, generation)
        run.signatures = dict(run.signatures, **{'blocked:' + signature: True})


def add_usage(run_id, generation, usage):
    with transaction() as db:
        run = leased(db, run_id, generation)
        run.usage = run.usage + usage

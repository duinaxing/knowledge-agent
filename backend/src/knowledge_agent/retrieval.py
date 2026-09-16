import math
import time
from functools import lru_cache
from datetime import datetime, timezone
import jieba
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.orm import defer
from .db import Document, Version, Chunk, Project
from .security import AppError, can_read, require_read, check_epoch, read_filter
from .documents import excerpt
from .config import settings
from . import models

jieba.setLogLevel(40)


@lru_cache(maxsize=4096)
def tokens(text):
    return tuple(x.lower() for x in jieba.lcut(text) if x.strip())


@lru_cache(maxsize=8)
def lexical_index(corpus):
    return BM25Okapi([tokens(text) for text in corpus])


def visible_projects(db, who):
    return list(db.scalars(select(Project).where(read_filter(who,'project',Project)).order_by(Project.code)))


def resolve_project(db, who, ref):
    matches = [p for p in visible_projects(db, who) if ref in [p.id, p.code, p.name, *p.aliases]]
    if not matches:
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    if len(matches) > 1:
        return None, [{'project_id': p.id, 'code': p.code, 'name': p.name} for p in matches[:5]]
    return matches[0], []


def project_evidence(p, kind, role=None):
    result = {'type': kind, 'project_id': p.id, 'code': p.code, 'name': p.name,
              'revision': p.revision, 'updated_at': p.updated_at, 'observed_at': time.time()}
    if kind == 'project_owner':
        result['people'] = [x for x in p.people if role is None or x['role'] == role]
    else:
        for key in ('phase', 'health', 'baseline_due_date', 'planned_due_date', 'actual_delivery_date', 'blockers','summary'):
            result[key] = getattr(p, key)
    return result


def search(db, who, args, expected_epoch, timeout=5, keyword_only=False):
    deadline = time.monotonic() + timeout
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise AppError('TIMEOUT', 504)
        return value
    check_epoch(db, expected_epoch)
    if args.project_id:
        require_read(db, who, 'project', args.project_id)
    # Authorization is performed before loading any chunks or scoring either index.
    doc_query=select(Document).where(read_filter(who,'document',Document))
    if args.project_id:
        doc_query=doc_query.where(Document.project_id==args.project_id)
    docs=list(db.scalars(doc_query))
    if not docs:
        return [], []
    when = datetime.combine(args.as_of, datetime.min.time(), tzinfo=timezone.utc).timestamp() if args.as_of else time.time()
    versions = list(db.scalars(select(Version).where(Version.document_id.in_([d.id for d in docs]),
                         Version.processing == 'ready', Version.publication == 'published',
                         Version.effective_from <= when)))
    if args.mode == 'current' or args.as_of:
        versions = [v for v in versions if v.effective_to is None or when < v.effective_to]
    if not versions:
        return [], []
    pg=db.bind.dialect.name=='postgresql'
    chunk_query=select(Chunk).where(Chunk.version_id.in_([v.id for v in versions])).order_by(Chunk.id)
    if pg:
        chunk_query=chunk_query.options(defer(Chunk.embedding))
    chunks = list(db.scalars(chunk_query))
    if not chunks:
        return [], []
    corpus=tuple(c.text for c in chunks)
    bm = lexical_index(corpus) if len(chunks)<=5000 and sum(len(t) for t in corpus)<=500000 else BM25Okapi([tokens(c.text) for c in chunks])
    scores = bm.get_scores(tokens(args.query))
    query_tokens = set(tokens(args.query))
    keyword = sorted((i for i,c in enumerate(chunks) if query_tokens.intersection(tokens(c.text))), key=lambda i: -scores[i])[:20]
    usage = []
    ranks = [keyword]
    if not keyword_only:
        model_id = 'TEST_ONLY_HASH' if settings.model_mode == 'test' else settings.embedding_model
        if any(v.embedding_model != model_id or v.embedding_dim != settings.embedding_dim for v in versions):
            raise AppError('INDEX_MODEL_MISMATCH', 409)
        check_epoch(db, expected_epoch)
        vectors, use = models.embed([args.query], remaining())
        usage.append(use)
        vector = vectors[0]
        def similarity(c):
            v = list(c.embedding)
            return sum(a*b for a,b in zip(vector, v)) / ((math.sqrt(sum(a*a for a in vector))*math.sqrt(sum(a*a for a in v))) or 1)
        if pg:
            ids=list(db.scalars(select(Chunk.id).where(Chunk.version_id.in_([v.id for v in versions]))
                     .order_by(Chunk.embedding.cosine_distance(vector),Chunk.id).limit(20)))
            positions={c.id:i for i,c in enumerate(chunks)}
            ranks.append([positions[id_] for id_ in ids])
        else:
            ranks.append(sorted(range(len(chunks)), key=lambda i: -similarity(chunks[i]))[:20])
    rrf = {}
    for rank in ranks:
        for position, i in enumerate(rank):
            rrf[i] = rrf.get(i, 0) + 1 / (60 + position + 1)
    version_map = {v.id: v for v in versions}
    evidence = []
    for i in sorted(rrf, key=lambda x: -rrf[x])[:20]:
        c = chunks[i]
        v = version_map[c.version_id]
        e = excerpt(db, who, v.document_id, v.id, c.id)
        e['query_mode'], e['as_of'] = args.mode, args.as_of.isoformat() if args.as_of else None
        evidence.append(e)
    check_epoch(db, expected_epoch)
    if not keyword_only and evidence:
        evidence = models.rerank(args.query, evidence, remaining())
    remaining()
    check_epoch(db, expected_epoch)
    return evidence[:min(args.top_k, 6)], usage

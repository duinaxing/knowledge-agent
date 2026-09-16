import hashlib
import os
import json
import subprocess
import sys
import time
from pathlib import Path
from sqlalchemy import select, delete
from .config import settings
from .db import Document, Version, Chunk, Job, uid
from .security import AppError, bump_epoch, require_read
from . import models


def upload(db, document_id, label, filename, body):
    suffix = Path(filename or '').suffix.lower()
    if suffix not in ('.md', '.txt', '.pdf'):
        raise AppError('UNSUPPORTED_FORMAT', 415)
    if not body or len(body) > settings.max_upload_bytes:
        raise AppError('INVALID_FILE_SIZE', 413)
    doc = db.get(Document, document_id)
    if not doc or doc.deleted_at:
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    if db.scalar(select(Version.id).where(Version.document_id == document_id, Version.version_label == label)):
        raise AppError('VERSION_EXISTS', 409)
    file_id = uid()
    settings.file_root.mkdir(parents=True, exist_ok=True)
    path = settings.file_root.resolve() / file_id
    path.write_bytes(body)
    db.info.setdefault('uploaded_files',[]).append(path)
    version = Version(id=file_id, document_id=document_id, version_label=label,
        content_hash=hashlib.sha256(body).hexdigest(), path=file_id, suffix=suffix)
    db.add(version)
    db.add(Job(kind='index', target_id=file_id))
    return version


def parse_file(version):
    path = (settings.file_root / version.path).resolve()
    if path.parent != settings.file_root.resolve():
        raise AppError('INVALID_FILE_PATH')
    try:
        # Separate process, strict deadline, output cap checked by parser.
        parser_env = {k: v for k, v in os.environ.items() if k.upper() in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP')}
        parser_env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        result = subprocess.run([sys.executable, '-m', 'knowledge_agent.parser', str(path),
            version.suffix, str(settings.max_pdf_pages)], capture_output=True, timeout=15,
            env=parser_env)
        if result.returncode:
            raise AppError('PARSE_FAILED')
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        raise AppError('PARSE_TIMEOUT') from None
    except (OSError, ValueError):
        raise AppError('PARSE_FAILED') from None


def split_pages(pages):
    chunks = []
    for page in pages:
        text = page['text']
        for offset in range(0, len(text), 500):
            block = text[offset:offset + 600]
            if block.strip():
                chunks.append((block, {'page': page.get('page'), 'offset': offset,
                                     'length': len(block), 'section': page.get('section', '')}))
    return chunks


def publish(db, version_id, body):
    version = db.get(Version, version_id)
    if not version:
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    doc = db.scalar(select(Document).where(Document.id == version.document_id).with_for_update())
    if doc.deleted_at or doc.revision != body.revision:
        raise AppError('REVISION_CONFLICT', 409)
    if version.processing != 'ready' or version.publication != 'draft':
        raise AppError('VERSION_NOT_READY', 409)
    start = body.effective_from.timestamp()
    end = body.effective_to.timestamp() if body.effective_to else None
    others = list(db.scalars(select(Version).where(Version.document_id == doc.id,
                         Version.publication == 'published')))
    for old in others:
        if old.effective_from < start and (old.effective_to is None or old.effective_to > start):
            old.effective_to = start
        elif (end is None or old.effective_from < end) and (old.effective_to is None or old.effective_to > start):
            raise AppError('OVERLAPPING_VERSION', 409)
    version.publication = 'published'
    version.effective_from, version.effective_to = start, end
    doc.revision += 1
    bump_epoch(db)


def delete_document(db, document_id):
    doc = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if not doc:
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    if not doc.deleted_at:
        doc.deleted_at = time.time()
        doc.revision += 1
        bump_epoch(db)
        db.add(Job(kind='cleanup', target_id=doc.id))


def excerpt(db, who, document_id, version_id, chunk_id):
    doc = require_read(db, who, 'document', document_id)
    version, chunk = db.get(Version, version_id), db.get(Chunk, chunk_id)
    if not version or not chunk or version.document_id != doc.id or chunk.version_id != version.id:
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    if version.publication != 'published' or version.processing != 'ready' or version.effective_from > time.time():
        raise AppError('NOT_FOUND_OR_FORBIDDEN', 404)
    return {'type': 'document', 'document_id': doc.id, 'version_id': version.id, 'chunk_id': chunk.id,
            'title': doc.title, 'text': chunk.text, 'locator': chunk.locator,
            'effective_from': version.effective_from, 'effective_to': version.effective_to,
            'version_label': version.version_label}

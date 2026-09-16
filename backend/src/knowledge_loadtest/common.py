import json
import os
import uuid
from pathlib import Path
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[3]
AREA = ROOT / 'runtime' / 'loadtest'
DB_NAME = 'knowledge_loadtest_100'


def guard(url=None, files=None):
    url = make_url(url or os.environ.get('DATABASE_URL', ''))
    if url.drivername != 'postgresql+psycopg' or url.database != DB_NAME or url.host not in ('127.0.0.1', 'localhost'):
        raise RuntimeError('Refusing non-isolated database')
    path = Path(files or os.environ.get('FILE_ROOT', '')).resolve()
    if not AREA.resolve().is_relative_to(ROOT.resolve()) or not path.is_relative_to(AREA.resolve()) or path != (AREA / 'files').resolve():
        raise RuntimeError('Refusing non-isolated file root')
    return url


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp,path)


def pdf_bytes(lines):
    """Small ASCII text PDF fixture, with a valid xref and extractable text."""
    content = 'BT /F1 10 Tf 40 790 Td 14 TL\n'
    for line in lines:
        escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        content += f'({escaped}) Tj T*\n'
    content += 'ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        f'<< /Length {len(content.encode())} >>\nstream\n{content}\nendstream'.encode()]
    data = bytearray(b'%PDF-1.4\n'); offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data.extend(f'{i} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 6\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]: data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(data)

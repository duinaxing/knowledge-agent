"""Child process parser. Container further enforces memory, PIDs and filesystem limits."""
import json
import sys
from pathlib import Path


def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    except ImportError:
        pass  # Windows: process timeout applies; Linux container supplies hard limits.
    path, suffix, max_pages = sys.argv[1:]
    if suffix == '.pdf':
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > int(max_pages):
            raise ValueError('UNSUPPORTED_PDF')
        pages = [{'page': i + 1, 'text': p.extract_text() or ''} for i, p in enumerate(reader.pages)]
        if any(not p['text'].strip() for p in pages):
            raise ValueError('OCR_REQUIRED')
    else:
        text = Path(path).read_text(encoding='utf-8-sig')
        if '\x00' in text:
            raise ValueError('INVALID_TEXT')
        pages = [{'page': None, 'text': text}]
    if not pages or not any(p['text'].strip() for p in pages) or sum(len(p['text']) for p in pages) > 2_000_000:
        raise ValueError('EMPTY_OR_TOO_LARGE')
    sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode())


if __name__ == '__main__':
    main()

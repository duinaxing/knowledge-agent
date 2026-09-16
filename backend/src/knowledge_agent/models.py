"""Provider adapters. No credentials or provider response bodies enter logs."""
import hashlib
import asyncio
import json
import math
import time
import httpx
from .config import settings
from .security import AppError


def _post(base, path, key, payload, timeout):
    if not base or not key:
        raise AppError('MODEL_NOT_CONFIGURED', 503)
    async def request():
        # asyncio timeout covers connect + all reads, including a slow continuous body.
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                async with client.stream('POST', base.rstrip('/') + path,
                        headers={'Authorization': 'Bearer ' + key}, json=payload) as response:
                    if response.status_code in (408, 429, 500, 502, 503, 504):
                        raise AppError('UNAVAILABLE', 503)
                    if response.status_code != 200:
                        raise AppError('PROVIDER_REJECTED', 502)
                    parts=[]
                    size=0
                    async for part in response.aiter_bytes():
                        size += len(part)
                        if size > 4 * 1024 * 1024:
                            raise AppError('MODEL_RESPONSE_TOO_LARGE', 502)
                        parts.append(part)
                    return json.loads(b''.join(parts))
    try:
        return asyncio.run(request())
    except (httpx.TimeoutException, TimeoutError):
        raise AppError('TIMEOUT', 504) from None
    except (httpx.HTTPError, ValueError):
        raise AppError('UNAVAILABLE', 503) from None


def embed(texts, timeout=5):
    if settings.model_mode == 'test':
        # Deliberately non-semantic; never counted as a real embedding evaluation.
        vectors = []
        for text in texts:
            vec = [0.0] * settings.embedding_dim
            for char in text:
                vec[int(hashlib.sha256(char.encode()).hexdigest()[:8], 16) % len(vec)] += 1
            length = math.sqrt(sum(x*x for x in vec)) or 1
            vectors.append([x / length for x in vec])
        return vectors, {'type': 'embedding', 'model': 'TEST_ONLY_HASH', 'usage': None}
    if not settings.embedding_model:
        raise AppError('EMBEDDING_NOT_CONFIGURED', 503)
    data = _post(settings.embedding_base_url, '/embeddings',
                 settings.embedding_api_key.get_secret_value(),
                 {'model': settings.embedding_model, 'input': texts}, timeout)
    try:
        rows = sorted(data['data'], key=lambda x: x['index'])
        if [r['index'] for r in rows] != list(range(len(texts))):
            raise ValueError()
        vectors = [r['embedding'] for r in rows]
        if any(len(v) != settings.embedding_dim or not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError()
        return vectors, {'type': 'embedding', 'model': settings.embedding_model, 'usage': data.get('usage')}
    except (KeyError, TypeError, ValueError):
        raise AppError('EMBEDDING_DIMENSION_MISMATCH', 502) from None


def chat(messages, tools=None, timeout=20, json_output=False):
    if settings.model_mode == 'test':
        raise AppError('TEST_MODEL_REQUIRES_INJECTION', 503)
    payload = {'model': settings.model_name, 'messages': messages, 'max_tokens': 1800,
               'stream': False, 'temperature': 0, 'thinking': {'type': 'disabled'}}
    if tools:
        payload['tools'] = tools
    if json_output:
        payload['response_format'] = {'type': 'json_object'}
    start = time.monotonic()
    data = _post(settings.model_base_url, '/chat/completions',
                 settings.model_api_key.get_secret_value(), payload, timeout)
    try:
        return data['choices'][0]['message'], {'type': 'generation', 'model': data.get('model', settings.model_name),
                'usage': data.get('usage'), 'seconds': time.monotonic() - start}
    except (KeyError, IndexError, TypeError):
        raise AppError('INVALID_MODEL_RESPONSE', 502) from None


def rerank(query, evidence, timeout=5):
    if not settings.reranker_url:
        return evidence
    data = _post(settings.reranker_url, '/rerank', settings.reranker_api_key.get_secret_value(),
                 {'model': settings.reranker_model, 'query': query,
                  'documents': [e['text'] for e in evidence], 'top_n': min(6, len(evidence))}, timeout)
    try:
        indices = [r['index'] for r in data['results']]
        if len(set(indices)) != len(indices) or any(i < 0 or i >= len(evidence) for i in indices):
            raise ValueError()
        return [evidence[i] for i in indices]
    except (KeyError, TypeError, ValueError):
        raise AppError('INVALID_RERANK_RESPONSE', 502) from None

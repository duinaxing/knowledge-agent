import asyncio
import json
import time
import httpx
import pytest
from knowledge_agent import models
from knowledge_agent.security import AppError


def use_transport(monkeypatch, handler):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw: original(transport=httpx.MockTransport(handler),**kw))


def test_provider_total_timeout(monkeypatch):
    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):
                await asyncio.sleep(.02)
                yield b' '
    use_transport(monkeypatch,lambda req:httpx.Response(200,stream=SlowBody()))
    start=time.monotonic()
    with pytest.raises(AppError) as exc:models._post('https://model.example','/chat','test',{},.07)
    assert exc.value.code=='TIMEOUT' and time.monotonic()-start<.3


@pytest.mark.parametrize('status,code',[(401,'PROVIDER_REJECTED'),(429,'UNAVAILABLE'),(503,'UNAVAILABLE')])
def test_provider_errors_are_redacted(monkeypatch,status,code):
    use_transport(monkeypatch,lambda req:httpx.Response(status,json={'error':'DO_NOT_LOG_SECRET'}))
    with pytest.raises(AppError) as exc:models._post('https://model.example','/chat','test',{},1)
    assert exc.value.code==code and 'DO_NOT_LOG' not in str(exc.value)


def test_provider_response_size_bound(monkeypatch):
    use_transport(monkeypatch,lambda req:httpx.Response(200,content=b'a'*(4*1024*1024+1)))
    with pytest.raises(AppError) as exc:models._post('https://model.example','/chat','test',{},1)
    assert exc.value.code=='MODEL_RESPONSE_TOO_LARGE'

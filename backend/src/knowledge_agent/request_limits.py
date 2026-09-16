"""Bound request bytes before JSON/multipart parsing, including chunked bodies."""
import asyncio
from starlette.responses import JSONResponse
from .config import settings


class RequestLimits:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] not in ('POST','PUT','PATCH'):
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        is_upload=scope['path'].startswith('/api/admin/documents') and headers.get(b'content-type',b'').startswith(b'multipart/form-data')
        maximum=settings.max_upload_bytes+256*1024 if is_upload else 64*1024
        async def reject(code,status):
            await JSONResponse({'error_code':code},status_code=status,headers={'Cache-Control':'no-store'})(scope,receive,send)
        try:
            declared=int(headers.get(b'content-length',b'0'))
        except ValueError:
            return await reject('INVALID_ARGUMENT',400)
        if declared<0:return await reject('INVALID_ARGUMENT',400)
        if declared>maximum:return await reject('REQUEST_TOO_LARGE',413)
        chunks=[];size=0
        try:
            async with asyncio.timeout(20):
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect':return
                    block=message.get('body',b'');size+=len(block)
                    if size>maximum:return await reject('REQUEST_TOO_LARGE',413)
                    chunks.append(block)
                    if not message.get('more_body',False):break
        except TimeoutError:
            return await reject('REQUEST_TIMEOUT',408)
        sent=False
        async def replay():
            nonlocal sent
            if not sent:
                sent=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)

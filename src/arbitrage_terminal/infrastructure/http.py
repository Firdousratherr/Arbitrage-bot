from __future__ import annotations
import asyncio,time,httpx
class HttpError(RuntimeError):
    def __init__(self,message,status=None):self.status=status;super().__init__(message)
class ResilientHttp:
    def __init__(self,timeout=30,retries=2):self.timeout=timeout;self.retries=retries;self.client=None
    async def start(self):self.client=httpx.AsyncClient(timeout=self.timeout,limits=httpx.Limits(max_connections=50,max_keepalive_connections=20))
    async def close(self):
        if self.client:await self.client.aclose()
    async def request(self,method,url,**kwargs):
        if not self.client:raise RuntimeError('HTTP client not started')
        for attempt in range(self.retries+1):
            try:
                started=time.perf_counter();r=await self.client.request(method,url,**kwargs)
                if r.status_code in {429,500,502,503,504} and attempt<self.retries:
                    await asyncio.sleep(float(r.headers.get('Retry-After','0') or 0) or .25*2**attempt);continue
                if r.status_code>=400:raise HttpError(f'HTTP {r.status_code}',r.status_code)
                return r,(time.perf_counter()-started)*1000,attempt
            except (httpx.TimeoutException,httpx.NetworkError) as e:
                if attempt<self.retries:await asyncio.sleep(.25*2**attempt);continue
                raise HttpError(f'{type(e).__name__}: {e}') from e

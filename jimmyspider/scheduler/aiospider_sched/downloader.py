"""
asyncio 下载器 (基于 aiohttp)

核心设计:
  - aiohttp.ClientSession: 异步 HTTP 客户端
  - 连接池复用: 减少 TCP 握手开销
  - 中间件链: 与 Scrapy 风格相同，但协程原生支持
  - 超时控制: ClientTimeout

对比 Scrapy 下载器:
  - 使用 asyncio + aiohttp 而非 ThreadPoolExecutor + requests
  - 天然非阻塞，更高并发
  - 无需线程切换开销
"""

import asyncio
import time
import logging
from typing import Optional

import aiohttp
from aiohttp import ClientTimeout, ClientSession, TCPConnector

from ..common.request import Request, Response
from ..common.middleware import DownloaderMiddleware
from .signals import AioSignalManager

logger = logging.getLogger(__name__)


class AioDownloaderMiddlewareManager:
    """
    asyncio 下载器中间件管理器

    与 Scrapy 版本接口一致，但支持协程中间件
    """

    def __init__(self, middlewares: list[DownloaderMiddleware] = None):
        self.middlewares = middlewares or []

    async def process_request(self, request: Request):
        """异步 process_request 链"""
        for mw in self.middlewares:
            try:
                result = mw.process_request(request)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    if isinstance(result, Response):
                        return "response", result
                    elif isinstance(result, Request):
                        request = result
            except Exception as e:
                logger.error(f"中间件 {mw.__class__.__name__}.process_request 异常: {e}")
        return "request", request

    async def process_response(self, request: Request, response: Response):
        """异步 process_response 链"""
        for mw in reversed(self.middlewares):
            try:
                result = mw.process_response(request, response)
                if asyncio.iscoroutine(result):
                    result = await result
                if isinstance(result, Request):
                    return "retry", result
                elif result is not None:
                    response = result
            except Exception as e:
                logger.error(f"中间件 {mw.__class__.__name__}.process_response 异常: {e}")
        return "response", response


class AioDownloader:
    """
    asyncio 下载器 (基于 aiohttp)

    配置:
      - concurrent_requests: 连接池大小
      - download_timeout: 超时时间
      - retry_times: 重试次数
    """

    def __init__(self, middlewares: list[DownloaderMiddleware] = None,
                 concurrent_requests: int = 100,
                 download_timeout: int = 30,
                 retry_times: int = 3,
                 signals: AioSignalManager = None):
        self.mw_manager = AioDownloaderMiddlewareManager(middlewares)
        self.concurrent_requests = concurrent_requests
        self.download_timeout = download_timeout
        self.retry_times = retry_times
        self.signals = signals or AioSignalManager()
        self._session: Optional[ClientSession] = None

    async def _get_session(self) -> ClientSession:
        if self._session is None or self._session.closed:
            connector = TCPConnector(
                limit=self.concurrent_requests,
                limit_per_host=10,
                ttl_dns_cache=300,
            )
            timeout = ClientTimeout(total=self.download_timeout)
            self._session = ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": "AioSpider/1.0"},
            )
        return self._session

    async def download(self, request: Request) -> Optional[Response]:
        """
        异步下载单个请求

        流程:
          1. process_request 中间件链
          2. aiohttp HTTP 请求
          3. process_response 中间件链
        """
        # Step 1: process_request
        kind, result = await self.mw_manager.process_request(request)
        if kind == "response":
            return result
        request = result

        # Step 2: HTTP 下载
        session = await self._get_session()
        try:
            start = time.time()
            async with session.request(
                method=request.method,
                url=request.url,
                headers=request.headers,
                data=request.body,
                allow_redirects=True,
            ) as resp:
                body = await resp.read()
                elapsed = time.time() - start

                response = Response(
                    url=request.url,
                    status=resp.status,
                    headers=dict(resp.headers),
                    body=body,
                    request=request,
                )
                logger.debug(f"下载完成 {request.url} [{resp.status}] {elapsed:.2f}s")

        except aiohttp.ClientError as e:
            logger.error(f"下载失败 {request.url}: {e}")
            return None
        except asyncio.coroutines.TimeoutError:
            logger.error(f"下载超时 {request.url}")
            return None

        # Step 3: process_response
        kind, result = await self.mw_manager.process_response(request, response)
        if kind == "retry":
            return await self.download(result)
        return result

    async def download_batch(self, requests: list[Request]) -> list[Optional[Response]]:
        """
        批量异步下载（使用 asyncio.gather）

        优势: 真正的并发，而非线程池
        """
        tasks = [self.download(req) for req in requests]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

"""
Scrapy 风格下载器

核心设计:
  - 中间件链: 多个 DownloaderMiddleware 串联
  - process_request 链 → HTTP 下载 → process_response 链
  - 支持 CONCURRENT_REQUESTS 并发控制
  - 使用 requests 库模拟 Scrapy 的下载行为

类比 Scrapy 源码:
  scrapy.core.downloader.Downloader
  scrapy.core.downloader.middleware.DownloaderMiddlewareManager
"""

import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..common.request import Request, Response
from ..common.middleware import DownloaderMiddleware
from .signals import SignalManager

logger = logging.getLogger(__name__)


class DownloaderMiddlewareManager:
    """
    下载器中间件管理器

    管理中间件链的 process_request → download → process_response 流程

    类比 Scrapy 的 DownloaderMiddlewareManager
    """

    def __init__(self, middlewares: list[DownloaderMiddleware] = None):
        self.middlewares: list[DownloaderMiddleware] = middlewares or []
        self.methods = {
            "process_request": [],
            "process_response": [],
            "process_exception": [],
        }
        for mw in self.middlewares:
            mw_name = mw.__class__.__name__
            if hasattr(mw, "process_request"):
                self.methods["process_request"].append((mw_name, mw.process_request))
            if hasattr(mw, "process_response"):
                self.methods["process_response"].append((mw_name, mw.process_response))
            if hasattr(mw, "process_exception"):
                self.methods["process_exception"].append((mw_name, mw.process_exception))

    def _process_request(self, request: Request) -> tuple:
        """执行 process_request 中间件链"""
        for name, method in self.methods["process_request"]:
            try:
                result = method(request)
                if result is not None:
                    if isinstance(result, Response):
                        logger.debug(f"中间件 {name}.process_request 返回 Response，短路")
                        return "response", result
                    elif isinstance(result, Request):
                        request = result
            except Exception as e:
                logger.error(f"中间件 {name}.process_request 异常: {e}")
        return "request", request

    def _process_response(self, request: Request, response: Response):
        """执行 process_response 中间件链"""
        for name, method in reversed(self.methods["process_response"]):
            try:
                result = method(request, response)
                if isinstance(result, Request):
                    logger.debug(f"中间件 {name}.process_response 返回 Request，重新下载")
                    return "retry", result
                elif result is not None:
                    response = result
            except Exception as e:
                logger.error(f"中间件 {name}.process_response 异常: {e}")
        return "response", response

    def _process_exception(self, request: Request, exception: Exception):
        """执行 process_exception 中间件链"""
        for name, method in reversed(self.methods["process_exception"]):
            try:
                result = method(request, exception)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(f"中间件 {name}.process_exception 异常: {e}")
        return None


class ScrapyDownloader:
    """
    Scrapy 风格下载器

    核心流程:
      1. 中间件 process_request 链
      2. HTTP 请求 (requests 库，线程池并发)
      3. 中间件 process_response 链

    配置:
      - CONCURRENT_REQUESTS: 并发请求数 (默认 16)
      - DOWNLOAD_TIMEOUT: 超时时间 (默认 30s)
      - RETRY_TIMES: 重试次数 (默认 3)
    """

    def __init__(self, middlewares: list[DownloaderMiddleware] = None,
                 concurrent_requests: int = 16,
                 download_timeout: int = 30,
                 retry_times: int = 3,
                 signals: SignalManager = None):
        self.mw_manager = DownloaderMiddlewareManager(middlewares)
        self.concurrent_requests = concurrent_requests
        self.download_timeout = download_timeout
        self.retry_times = retry_times
        self.signals = signals or SignalManager()

        # HTTP Session（线程安全，连接池复用）
        self._session: Optional[requests.Session] = None
        self._executor = ThreadPoolExecutor(max_workers=concurrent_requests)
        self._active = 0
        self._lock = threading.Lock()

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            retry = Retry(
                total=self.retry_times,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
            )
            adapter = HTTPAdapter(
                max_retries=retry,
                pool_connections=20,
                pool_maxsize=20,
            )
            self._session.mount("http://", adapter)
            self._session.mount("https://", adapter)
        return self._session

    def download(self, request: Request) -> Optional[Response]:
        """
        下载单个请求（同步）

        流程:
          1. process_request 中间件链
          2. HTTP 请求
          3. process_response 中间件链
        """
        # Step 1: process_request
        kind, result = self.mw_manager._process_request(request)
        if kind == "response":
            return result  # 中间件短路返回了 Response
        request = result

        # Step 2: HTTP 下载
        session = self._get_session()
        try:
            start = time.time()
            http_resp = session.request(
                method=request.method,
                url=request.url,
                headers=request.headers,
                data=request.body,
                timeout=self.download_timeout,
                allow_redirects=True,
            )
            elapsed = time.time() - start

            response = Response(
                url=request.url,
                status=http_resp.status_code,
                headers=dict(http_resp.headers),
                body=http_resp.content,
                request=request,
            )
            logger.debug(f"下载完成 {request.url} [{http_resp.status_code}] {elapsed:.2f}s")

        except requests.RequestException as e:
            logger.error(f"下载失败 {request.url}: {e}")
            # 尝试中间件异常处理
            result = self.mw_manager._process_exception(request, e)
            if isinstance(result, Response):
                return result
            return None

        # Step 3: process_response
        kind, result = self.mw_manager._process_response(request, response)
        if kind == "retry":
            return self.download(result)  # 递归重试
        return result

    def download_batch(self, requests: list[Request]) -> list[Optional[Response]]:
        """
        批量下载（线程池并发）

        返回顺序与输入顺序相同
        """
        futures = {}
        for i, req in enumerate(requests):
            future = self._executor.submit(self.download, req)
            futures[future] = i

        results = [None] * len(requests)
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result(timeout=self.download_timeout + 5)
            except Exception as e:
                logger.error(f"批量下载异常 index={idx}: {e}")

        return results

    def close(self):
        self._executor.shutdown(wait=True)
        if self._session:
            self._session.close()

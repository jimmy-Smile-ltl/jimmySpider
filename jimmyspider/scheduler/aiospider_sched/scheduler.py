"""
asyncio 调度器

核心设计:
  - asyncio.PriorityQueue: 异步优先级队列
  - 去重过滤器: 与 Scrapy 版本共用 RFPDupeFilter
  - 速率限制: asyncio.Semaphore 控制并发
  - 域级延迟: 按域名独立延迟控制

对比 Scrapy 调度器:
  - 使用 asyncio.Queue 而非 heapq+threading
  - async/await 原生非阻塞
  - 天然支持协程式暂停/恢复
"""

import asyncio
import time
import logging
from typing import Optional
from collections import defaultdict

from ..common.request import Request
from ..scrapy_sched.scheduler import RFPDupeFilter

logger = logging.getLogger(__name__)


class AioPriorityQueue:
    """
    asyncio 优先级队列

    封装 asyncio.PriorityQueue，支持 priority 排序
    """

    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=maxsize)
        self._counter = 0

    async def put(self, request: Request) -> None:
        """入队: priority 大的先出（取反）"""
        self._counter += 1
        await self._queue.put((-request.priority, self._counter, request))

    async def get(self) -> Request:
        """出队"""
        _, _, request = await self._queue.get()
        return request

    def put_nowait(self, request: Request) -> None:
        """非阻塞入队"""
        self._counter += 1
        self._queue.put_nowait((-request.priority, self._counter, request))

    def get_nowait(self) -> Optional[Request]:
        """非阻塞出队"""
        if self._queue.empty():
            return None
        _, _, request = self._queue.get_nowait()
        return request

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()


class DomainRateLimiter:
    """
    域级速率限制

    每个域名独立维护一个 asyncio.Semaphore + 最后请求时间
    """

    def __init__(self, default_delay: float = 1.0):
        self.default_delay = default_delay
        self._last_request: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # 域名并发限制
        self._domain_sem: dict[str, asyncio.Semaphore] = {}
        self.domain_concurrency: int = 3

    def get_domain(self, request: Request) -> str:
        """从 URL 提取域名"""
        from urllib.parse import urlparse
        return urlparse(request.url).netloc or "unknown"

    async def wait(self, request: Request, delay: float = None) -> None:
        """等待直到可以发送请求"""
        domain = self.get_domain(request)
        d = delay or self.default_delay

        async with self._locks[domain]:
            now = time.time()
            last = self._last_request.get(domain, 0)
            wait_time = d - (now - last)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_request[domain] = time.time()


class AioScheduler:
    """
    asyncio 调度器

    特性:
      - asyncio.PriorityQueue 异步队列
      - 域级速率限制
      - 信号量并发控制
    """

    def __init__(self, dupefilter: RFPDupeFilter = None,
                 concurrent_requests: int = 16,
                 domain_delay: float = 1.0):
        self.dupefilter = dupefilter or RFPDupeFilter()
        self.queue = AioPriorityQueue()
        self.semaphore = asyncio.Semaphore(concurrent_requests)
        self.rate_limiter = DomainRateLimiter(default_delay=domain_delay)
        self.stats = {"enqueued": 0, "dequeued": 0, "filtered": 0}

    async def enqueue_request(self, request: Request) -> bool:
        """异步入队"""
        if not request.dont_filter and self.dupefilter.request_seen(request):
            self.stats["filtered"] += 1
            return False
        await self.queue.put(request)
        self.stats["enqueued"] += 1
        return True

    async def next_request(self) -> Optional[Request]:
        """异步出队"""
        try:
            request = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            self.stats["dequeued"] += 1
            return request
        except asyncio.TimeoutError:
            return None

    async def acquire_slot(self, request: Request) -> None:
        """获取并发槽位"""
        await self.semaphore.acquire()
        await self.rate_limiter.wait(request)

    def release_slot(self) -> None:
        """释放并发槽位"""
        self.semaphore.release()

    def pending(self) -> int:
        return self.queue.qsize()

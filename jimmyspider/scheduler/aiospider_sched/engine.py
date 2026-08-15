"""
asyncio 爬虫引擎 (基于协程的调度循环)

核心设计:

    ┌─────────────────────────────────────────┐
    │              AioSpiderEngine              │
    │  (使用 asyncio.Task + asyncio.Queue)      │
    │                                           │
    │  ① get_request() ← AioScheduler (Queue)   │
    │       ↓                                   │
    │  ② download(req) → AioDownloader (aiohttp)│
    │       ↓ (每个请求一个 asyncio.Task)         │
    │  ③ scrape(response) → Spider (coroutine)  │
    │       ↓                                   │
    │  ④ enqueue(new_reqs) → Scheduler          │
    │       ↓                                   │
    │  loop back to ①                           │
    └─────────────────────────────────────────┘

对比 Scrapy 引擎:
  - 使用 asyncio.Task 代替 Twisted Deferred
  - 使用 asyncio.gather 实现真正并发
  - 使用 asyncio.Semaphore 做并发控制
  - 无 GIL 限制的协程级并发（适合 IO 密集型）

性能特点:
  - 100 并发请求只需 ~1 个线程
  - 内存占用远低于线程池模式
  - 天然支持超时取消 (asyncio.wait_for + cancel)
"""

import asyncio
import time
import logging
from typing import Optional

from ..common.request import Request, Response
from ..common.spider import BaseSpider
from ..common.middleware import DownloaderMiddleware, SpiderMiddleware

from .scheduler import AioScheduler
from .downloader import AioDownloader
from .spider_mw import AioSpiderMiddlewareManager
from .signals import AioSignalManager

logger = logging.getLogger(__name__)


class AioSpiderEngine:
    """
    asyncio 爬虫引擎

    配置:
        CONCURRENT_REQUESTS: 并发槽位数
        DOMAIN_DELAY: 域级延迟（秒）
        MAX_REQUESTS: 最大请求数
        BATCH_SIZE: 每次事件循环处理的请求数
    """

    def __init__(self, spider: BaseSpider,
                 downloader_middlewares: list[DownloaderMiddleware] = None,
                 spider_middlewares: list[SpiderMiddleware] = None,
                 concurrent_requests: int = 100,
                 domain_delay: float = 0.0,
                 max_requests: int = 0,
                 batch_size: int = 20,
                 signals: AioSignalManager = None):
        self.spider = spider
        self.signals = signals or AioSignalManager()

        # 组件
        self.scheduler = AioScheduler(
            concurrent_requests=concurrent_requests,
            domain_delay=domain_delay,
        )
        self.downloader = AioDownloader(
            middlewares=downloader_middlewares,
            concurrent_requests=concurrent_requests,
            signals=self.signals,
        )
        self.spider_mw = AioSpiderMiddlewareManager(spider_middlewares)

        # 配置
        self.concurrent_requests = concurrent_requests
        self.domain_delay = domain_delay
        self.max_requests = max_requests
        self.batch_size = batch_size

        # 状态
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self.stats = {
            "requests_scheduled": 0,
            "responses_received": 0,
            "items_scraped": 0,
        }

    # ---- 异步引擎生命周期 ----

    async def start(self) -> None:
        """异步启动引擎"""
        await self.signals.send("engine_started", engine=self)
        await self.signals.send("spider_opened", spider=self.spider)
        self._running = True
        logger.info(f"[AioEngine] 启动 Spider[{self.spider.name}] "
                    f"concurrent={self.concurrent_requests}")

    async def stop(self) -> None:
        """异步停止引擎"""
        self._running = False
        # 等待所有任务完成
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.downloader.close()
        await self.signals.send("spider_closed", spider=self.spider, reason="finished")
        await self.signals.send("engine_stopped", engine=self)
        logger.info(f"[AioEngine] 停止 {self.spider.summary()}")

    # ---- 调度循环 ----

    async def run(self) -> None:
        """
        主调度循环（协程）

        使用 asyncio.Task 并发处理多个请求
        每个请求的生命周期: schedule → download → scrape → enqueue
        """
        await self.start()

        # Step 0: 加载起始请求
        start_requests = list(self.spider.start_requests())
        for req in start_requests:
            await self._schedule_request(req)

        # 主循环
        batch = []
        while self._running:
            # Step 1: 从调度器获取请求
            request = await self._get_next_request()
            if request is None:
                # 没有新请求 — 检查是否空闲
                if self._idle() and not self._tasks:
                    break
                await asyncio.sleep(0.05)
                continue

            # Step 2: 获取并发槽位 + 创建处理 Task
            await self.scheduler.acquire_slot(request)

            task = asyncio.create_task(
                self._process_request(request),
                name=f"crawl_{request._id[:20]}"
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

            # 检查最大请求数
            if self.max_requests > 0 and self.spider.stats["response_count"] >= self.max_requests:
                logger.info(f"[AioEngine] 达到最大请求数 {self.max_requests}")
                break

            # 防止事件循环被 Task 创建占满
            if len(self._tasks) >= self.concurrent_requests * 2:
                await asyncio.sleep(0.01)

        # 等待所有任务完成
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        await self.stop()

    # ---- 处理单个请求的完整生命周期 ----

    async def _process_request(self, request: Request) -> None:
        """
        处理单个请求的完整生命周期（asyncio.Task 中执行）

        对应 Scrapy 的 ExecutionEngine._handle_downloader_output
        """
        try:
            # Step 1: 下载
            response = await self._download(request)
            if response is None:
                self.spider.stats["error_count"] += 1
                return

            self.spider.stats["response_count"] += 1

            # Step 2: 爬虫解析
            results = await self._scrape(response)

            # Step 3: 处理产出
            await self._process_results(results)

        except asyncio.CancelledError:
            logger.debug(f"任务取消: {request.url}")
        except Exception as e:
            logger.error(f"[AioEngine] 处理异常 {request.url}: {e}")
        finally:
            self.scheduler.release_slot()

    # ---- 内部异步方法 ----

    async def _schedule_request(self, request: Request) -> None:
        if await self.scheduler.enqueue_request(request):
            self.stats["requests_scheduled"] += 1
            self.spider.stats["request_count"] += 1
            await self.signals.send("request_scheduled", request=request)
        else:
            await self.signals.send("request_dropped", request=request, reason="duplicate")

    async def _get_next_request(self) -> Optional[Request]:
        return await self.scheduler.next_request()

    async def _download(self, request: Request) -> Optional[Response]:
        response = await self.downloader.download(request)
        if response:
            await self.signals.send("response_downloaded", response=response, request=request)
        return response

    async def _scrape(self, response: Response) -> list:
        """异步执行爬虫解析"""

        def scrape_func(resp):
            callback_name = resp.request.callback if resp.request else "parse"
            callback = getattr(self.spider, callback_name, self.spider.parse)
            result = callback(resp)
            return list(result) if result else []

        return await self.spider_mw.scrape_response(scrape_func, response)

    async def _process_results(self, results: list) -> None:
        for result in results:
            if isinstance(result, Request):
                await self._schedule_request(result)
            elif isinstance(result, dict):
                self.spider.process_item(result)
                self.stats["items_scraped"] += 1
                await self.signals.send("item_scraped", item=result)

    def _idle(self) -> bool:
        return self.scheduler.pending() == 0

    # ---- 测试用 ----

    async def download_single(self, request: Request) -> Optional[Response]:
        return await self._download(request)

"""
Scrapy 风格引擎 (基于回调链的调度循环)

核心设计（模拟 Scrapy Engine 的调度循环）:

    ┌─────────────────────────────────────────┐
    │                 Engine                    │
    │                                           │
    │  ① get_requests() ← Scheduler             │
    │       ↓                                   │
    │  ② download(req) → Downloader             │
    │       ↓                                   │
    │  ③ scrape(response) → Spider              │
    │       ↓                                   │
    │  ④ enqueue(new_requests) → Scheduler      │
    │       ↓                                   │
    │  ⑤ process_items(items) → Pipeline        │
    │       ↓                                   │
    │  loop back to ①                           │
    └─────────────────────────────────────────┘

类比 Scrapy 源码:
  scrapy.core.engine.ExecutionEngine
  scrapy.core.scraper.Scraper

关键区别:
  - Scrapy 使用 Twisted 的 Deferred 链
  - 本实现使用 Python 原生回调 + 迭代循环
  - 保留了相同的架构分层和中间件链
"""

import time
import logging
import threading
from typing import Optional

from ..common.request import Request, Response
from ..common.spider import BaseSpider
from ..common.middleware import DownloaderMiddleware, SpiderMiddleware

from .scheduler import ScrapyScheduler
from .downloader import ScrapyDownloader
from .spider_mw import SpiderMiddlewareManager
from .signals import SignalManager

logger = logging.getLogger(__name__)


class ScrapyEngine:
    """
    Scrapy 风格爬虫引擎

    核心调度循环 (类似 Scrapy 的 _next_request):

    使用回调链而非 Twisted Deferred，但保持了相同的架构模式:
      - Engine 是中心协调者
      - Scheduler 管理请求队列
      - Downloader 处理 HTTP + 下载器中间件
      - SpiderMiddlewareManager 处理爬虫中间件
      - ItemPipeline 处理输出

    配置参数:
        CONCURRENT_REQUESTS: 并发下载数
        DOWNLOAD_DELAY: 下载间隔
        RANDOMIZE_DOWNLOAD_DELAY: 随机延迟
        MAX_REQUESTS: 最大请求数
    """

    def __init__(self, spider: BaseSpider,
                 downloader_middlewares: list[DownloaderMiddleware] = None,
                 spider_middlewares: list[SpiderMiddleware] = None,
                 concurrent_requests: int = 16,
                 download_delay: float = 0.0,
                 max_requests: int = 0,
                 signals: SignalManager = None):
        self.spider = spider
        self.signals = signals or SignalManager()

        # 组件
        self.scheduler = ScrapyScheduler()
        self.downloader = ScrapyDownloader(
            middlewares=downloader_middlewares,
            concurrent_requests=concurrent_requests,
            signals=self.signals,
        )
        self.spider_mw = SpiderMiddlewareManager(spider_middlewares)

        # 配置
        self.concurrent_requests = concurrent_requests
        self.download_delay = download_delay
        self.max_requests = max_requests

        # 状态
        self._running = False
        self._paused = False
        self._slot_start_time = time.time()
        self.stats = {
            "requests_scheduled": 0,
            "responses_received": 0,
            "items_scraped": 0,
        }

    # ---- 引擎生命周期 ----

    def start(self) -> None:
        """启动引擎"""
        self.signals.send("engine_started", engine=self)
        self.signals.send("spider_opened", spider=self.spider)
        self._running = True
        logger.info(f"[Engine] 启动 Spider[{self.spider.name}]")

    def stop(self) -> None:
        """停止引擎"""
        self._running = False
        self.signals.send("spider_closed", spider=self.spider, reason="finished")
        self.signals.send("engine_stopped", engine=self)
        logger.info(f"[Engine] 停止 {self.spider.summary()}")

    # ---- 调度循环 ----

    def run(self) -> None:
        """
        主调度循环

        类比 Scrapy 的 ExecutionEngine._next_request 循环
        """
        self.start()

        # Step 0: 加载起始请求
        start_requests = list(self.spider.start_requests())
        for req in start_requests:
            self._schedule_request(req)

        # 主循环
        while self._running:
            # Step 1: 从调度器获取下一个请求
            request = self._get_next_request()
            if request is None:
                # 没有待处理请求，检查是否完成
                if self._idle():
                    break
                time.sleep(0.1)
                continue

            # Step 2: 下载
            response = self._download(request)
            if response is None:
                self.spider.stats["error_count"] += 1
                continue

            self.spider.stats["response_count"] += 1

            # Step 3: 爬虫中间件 + 爬虫解析
            results = self._scrape(response)

            # Step 4: 处理产出 (Request → 入队, dict → Pipeline)
            self._process_results(results)

            # 检查最大请求数
            if self.max_requests > 0 and self.spider.stats["response_count"] >= self.max_requests:
                logger.info(f"[Engine] 达到最大请求数 {self.max_requests}")
                break

        self.stop()

    # ---- 内部方法 ----

    def _schedule_request(self, request: Request) -> None:
        """将请求加入调度器"""
        if self.scheduler.enqueue_request(request):
            self.stats["requests_scheduled"] += 1
            self.spider.stats["request_count"] += 1
            self.signals.send("request_scheduled", request=request)
        else:
            self.signals.send("request_dropped", request=request, reason="duplicate")

    def _get_next_request(self) -> Optional[Request]:
        """从调度器获取下一个请求"""
        return self.scheduler.next_request()

    def _download(self, request: Request) -> Optional[Response]:
        """下载请求"""
        if self.download_delay > 0:
            elapsed = time.time() - self._slot_start_time
            if elapsed < self.download_delay:
                time.sleep(self.download_delay - elapsed)
            self._slot_start_time = time.time()

        response = self.downloader.download(request)
        if response:
            self.signals.send("response_downloaded", response=response, request=request)
        return response

    def _scrape(self, response: Response) -> list:
        """执行爬虫解析（通过 SpiderMiddleware 链）"""

        def scrape_func(resp):
            callback_name = resp.request.callback if resp.request else "parse"
            callback = getattr(self.spider, callback_name, self.spider.parse)
            result = callback(resp)
            return list(result) if result else []

        return self.spider_mw.scrape_response(scrape_func, response)

    def _process_results(self, results: list) -> None:
        """处理爬虫产出"""
        for result in results:
            if isinstance(result, Request):
                self._schedule_request(result)
            elif isinstance(result, dict):
                self.spider.process_item(result)
                self.stats["items_scraped"] += 1
                self.signals.send("item_scraped", item=result)
            else:
                logger.debug(f"忽略未知产出类型: {type(result)}")

    def _idle(self) -> bool:
        """检查引擎是否空闲（无待处理请求）"""
        return len(self.scheduler) == 0

    # ---- 下载器测试用 ----

    def download_single(self, request: Request) -> Optional[Response]:
        """单次下载（测试用）"""
        return self._download(request)

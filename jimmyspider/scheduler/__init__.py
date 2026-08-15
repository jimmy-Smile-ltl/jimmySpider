"""
jimmyspider.scheduler — Scrapy / AioScrapy 双风格调度器

两种爬虫调度器架构，均遵循 Scrapy 的五层架构
(Engine → Scheduler → Downloader → Spider Middleware → Spider)：

  - scrapy_sched:   Scrapy 风格 — 同步回调链 + 线程池 (requests)
  - aiospider_sched: AioScrapy 风格 — asyncio 协程 + 信号量 (aiohttp)
  - common:         共享模块 — Request / Response / BaseSpider / 中间件基类

使用示例：

    from jimmyspider.scheduler import BaseSpider, Request, ScrapyEngine

    class MySpider(BaseSpider):
        name = "my_spider"

        def start_requests(self):
            yield Request(url="https://example.com")

        def parse(self, response):
            yield {"title": response.text[:50]}

    ScrapyEngine(spider=MySpider(), concurrent_requests=16).run()

或使用 AioSpiderEngine (asyncio)：

    import asyncio
    from jimmyspider.scheduler import AioSpiderEngine

    asyncio.run(AioSpiderEngine(spider=MySpider(), concurrent_requests=100).run())

文档见 scheduler/docs/ (scheduler.md / ANALYSIS_REPORT.md)。
"""

from jimmyspider.scheduler.common.request import Request, Response
from jimmyspider.scheduler.common.spider import BaseSpider
from jimmyspider.scheduler.scrapy_sched.engine import ScrapyEngine
from jimmyspider.scheduler.aiospider_sched.engine import AioSpiderEngine
from jimmyspider.scheduler.scrapy_sched.scheduler import RFPDupeFilter
from jimmyspider.scheduler.aiospider_sched.scheduler import DomainRateLimiter

__all__ = [
    "Request",
    "Response",
    "BaseSpider",
    "ScrapyEngine",
    "AioSpiderEngine",
    "RFPDupeFilter",
    "DomainRateLimiter",
]

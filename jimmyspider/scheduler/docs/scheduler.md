# 爬虫调度器 — Scrapy vs AioScrapy 架构对比

两种爬虫调度器架构的完整实现、基准测试和文档（jimmySpider 内置模块）。均遵循 Scrapy 的五层架构（Engine → Scheduler → Downloader → Spider Middleware → Spider），但使用完全不同的并发模型。

## 目录结构

```
jimmyspider/scheduler/
├── __init__.py                  # 包入口: Request / Response / BaseSpider / 两种 Engine 等
├── common/                      # 共享模块 (两种风格通用)
│   ├── request.py               # Request / Response 数据结构
│   ├── spider.py                # BaseSpider 基类
│   └── middleware.py            # DownloaderMiddleware / SpiderMiddleware 基类
├── scrapy_sched/                # Scrapy 风格 (同步 + 线程池)
│   ├── engine.py                # ScrapyEngine — 迭代回调循环
│   ├── scheduler.py             # PriorityQueue + RFPDupeFilter + DiskQueue
│   ├── downloader.py            # ThreadPoolExecutor + requests + 中间件链
│   ├── spider_mw.py             # SpiderMiddlewareManager
│   └── signals.py               # 发布/订阅信号系统
├── aiospider_sched/             # AioSpider 风格 (异步 + 协程)
│   ├── engine.py                # AioSpiderEngine — asyncio.Task 循环
│   ├── scheduler.py             # asyncio.PriorityQueue + DomainRateLimiter
│   ├── downloader.py            # aiohttp + 协程中间件链
│   ├── spider_mw.py             # 异步 SpiderMiddlewareManager
│   └── signals.py               # 异步信号系统
├── analysis/
│   ├── benchmark.py             # 4 组基准测试
│   └── ... (基准测试数据见 docs/ANALYSIS_REPORT.md)
└── docs/
    ├── scheduler.md             # 本文档
    └── ANALYSIS_REPORT.md       # 综合对比报告
```

## 快速开始

```bash
pip install requests aiohttp      # 依赖 (jimmySpider 默认依赖已含)
```

## 两种风格的爬虫代码完全相同

```python
from jimmyspider.scheduler import BaseSpider, Request

class MySpider(BaseSpider):
    name = "my_spider"

    def start_requests(self):
        for page in range(1, 100):
            yield Request(url=f"https://example.com/page/{page}",
                         callback="parse_list")

    def parse_list(self, response):
        for url in extract_urls(response.text):
            yield Request(url=url, callback="parse_detail")

    def parse_detail(self, response):
        yield {"title": extract_title(response.text)}
```

## 启动方式

```python
# 方式 1: Scrapy 风格
from jimmyspider.scheduler import ScrapyEngine
engine = ScrapyEngine(spider=MySpider(), concurrent_requests=16)
engine.run()

# 方式 2: AioScrapy 风格
import asyncio
from jimmyspider.scheduler import AioSpiderEngine

async def main():
    engine = AioSpiderEngine(spider=MySpider(), concurrent_requests=100)
    await engine.run()

asyncio.run(main())
```

## 测试与基准

原仓库 (`spider research/爬虫架构/scheduler/`) 中附带 15 个测试用例
（Scrapy 风格 8 个 + AioScrapy 风格 7 个，含本地 HTTP 服务器集成测试、批量下载、域级限速），
本仓库仅保留模块源码，未携带测试文件。

对比基准测试（4 组 benchmark，启动本地 HTTP 服务器实测）：

```bash
python jimmyspider/scheduler/analysis/benchmark.py
```

## 测试结果（原仓库实测）

| 测试套件 | 用例数 | 通过 |
|---------|--------|------|
| Scrapy 风格 | 8 | 8 ✅ |
| AioScrapy 风格 | 7 | 7 ✅ |
| **合计** | **15** | **15 ✅** |

## 基准测试结果

### 吞吐量对比 (req/s)

| 并发数 | Scrapy | AioScrapy |
|--------|--------|-----------|
| 10 | 117.8 | 44.6 |
| 20 | 136.3 | 76.3 |
| 50 | 148.0 | 121.5 |
| 100 | 152.6 | 124.6 |

### 延迟分布对比 (100 请求, concurrency=50)

| 百分位 | Scrapy | AioScrapy | Aio 优势 |
|--------|--------|-----------|---------|
| P50 | 385.9ms | 163.6ms | **2.4x 低** |
| P90 | 668.7ms | 304.1ms | **2.2x 低** |
| P99 | 716.9ms | 329.5ms | **2.2x 低** |

## 架构对比

| 维度 | Scrapy 风格 | AioScrapy 风格 |
|------|-----------|---------------|
| 并发模型 | ThreadPoolExecutor | asyncio + Task |
| HTTP 客户端 | requests (同步) | aiohttp (异步) |
| 事件循环 | while 迭代循环 | asyncio 事件循环 |
| 流控方式 | 计数器 + sleep | asyncio.Semaphore |
| 域级延迟 | 不支持 | DomainRateLimiter |
| 并发上限 | ~100 线程 | ~10000+ 协程 |
| 每连接内存 | ~8MB (线程栈) | ~1KB (协程) |
| 取消支持 | 不支持 | asyncio.CancelledError |
| 超时控制 | socket timeout | asyncio.wait_for |
| 吞吐量 (本地) | 较高 | 中等 |
| 延迟 (本地) | 较高 | **2x 低** |
| **真实网络延迟** | 受线程切换影响 | **优势更明显** |

## 选型建议

```
选择 Scrapy 风格当:
  - 需要最大兼容性 (requests 生态)
  - CPU 密集型解析任务
  - 并发 < 50
  - 与现有同步代码集成

选择 AioScrapy 风格当:
  - IO 密集型 (大量 API 请求)
  - 需要高并发 (>100)
  - 对延迟敏感
  - 需要域级速率限制
  - 需要请求取消能力
  - 内存受限环境
```

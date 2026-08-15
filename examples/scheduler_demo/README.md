# scheduler_demo —— 调度引擎演示（ScrapyEngine vs AioSpiderEngine）

> 演示 jimmySpider 调度器模块（`jimmyspider/scheduler/`）的完整用法。
> 两种引擎共用同一套 `Request / Response / BaseSpider`，爬虫代码完全一致，
> 换引擎只需改一行 —— 本示例默认离线（内置 mock HTML，零依赖），也可抓真实 Hacker News。

## 运行方式

```bash
python spider.py                        # 默认：AioSpiderEngine + 离线 mock 数据
python spider.py --engine scrapy       # ScrapyEngine（同步回调链 + 线程池）
python spider.py --engine both         # 同一爬虫两种引擎对比（输出耗时）
python spider.py --mode online         # 抓真实 Hacker News（需联网）
python spider.py --limit 10 --concurrent 20   # 控制详情页数量 / 并发
```

结果写入本地 `results.db`（SQLite，stdlib，零依赖）。

## 它演示了什么

1. **五层架构**：Engine（调度循环）→ Scheduler（去重 + 优先级队列 + 限速）→
   Downloader（HTTP + 中间件链）→ SpiderMiddleware → Spider
2. **爬虫写法与 Scrapy 同构**：`start_requests()` 产出起始请求；
   `parse_list()` yield 详情页 `Request`（带 `callback` + `meta` 传数据）；
   `parse_detail()` yield `dict` → 引擎交给 `process_item()` 保存
3. **DownloaderMiddleware 短路**：离线模式用 `MockDownloaderMiddleware` 在
   `process_request` 直接返回 `Response`，完全绕开网络 —— 同样的中间件机制也用于
   代理、Cookie、缓存等场景
4. **`AioSpiderEngine`**：asyncio.Task + 信号量并发，IO 密集场景单线程撑数百并发

## ScrapyEngine vs AioSpiderEngine

| 维度 | ScrapyEngine（`scrapy_sched`） | AioSpiderEngine（`aiospider_sched`） |
|------|-------------------------------|-------------------------------------|
| 并发模型 | 同步回调链 + `ThreadPoolExecutor` + requests | asyncio 协程 + 信号量 + aiohttp |
| 队列 | heapq 优先级队列 + RFPDupeFilter 去重 | asyncio.PriorityQueue + 域级限速 |
| 中间件 | 同步链 | 协程原生（支持 async 中间件） |
| 典型并发 | 16~64（线程受 GIL/线程数限制） | 100~1000（协程开销极小） |
| 适用场景 | 简单脚本、依赖 requests 生态、快速上手 | 高并发 IO 密集、长链接、大量小请求 |

**怎么选**：任务少、追求简单 → `ScrapyEngine`；目标站多、请求量大、
并发要求高 → `AioSpiderEngine`。迁移成本为零（爬虫代码相同，仅换 Engine 一行）。

## 设计要点

- `dont_filter=True`：列表页每次运行都重爬（去重器按 URL 判重）
- 详情页 Request 把 `rank/title` 放 `meta`，`parse_detail` 从 `response.meta` 取回，
  避免二次解析列表页 —— 这是 Scrapy 惯用法
- 引擎按 `max_requests` 限流，避免演示超跑；真实项目可去掉
- 建议先 `python spider.py --engine both` 观察两种引擎在离线数据下的耗时差异

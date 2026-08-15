# scheduler_demo —— 调度引擎演示（ScrapyEngine vs AioSpiderEngine）

> 演示 jimmySpider 调度器模块（`jimmyspider/scheduler/`）的完整用法：两种引擎共用同一套 `Request / Response / BaseSpider`，爬虫代码完全一致，换引擎只需改一行。默认离线（内置 mock HTML，零依赖），也可抓真实 Hacker News。

## 站点

- 目标站点：https://news.ycombinator.com/ — Hacker News 首页（列表页 + 新闻详情页）
- 离线模式：`MockDownloaderMiddleware` 用内置 `MOCK_LIST_HTML` 与 `mock_html_for()` 生成响应，完全绕开网络
- 数据形态：列表页 `span.titleline > a` 取前 N 条 → 详情页 `span.score` 提取分数

## 展示特性

- **五层架构演示**：Engine（调度循环）→ Scheduler（去重 + 优先级队列 + 限速）→ Downloader（HTTP + 中间件链）→ SpiderMiddleware → Spider
- **爬虫写法与 Scrapy 同构**：`start_requests()` 产出起始请求；`parse_list()` yield 详情页 `Request`（带 `callback` + `meta` 传数据）；`parse_detail()` yield `dict` → 引擎交给 `process_item()` 保存
- **DownloaderMiddleware 短路**：`MockDownloaderMiddleware.process_request` 返回 `Response` 直接短路下载 —— 同样的中间件机制也用于代理、Cookie、缓存等场景
- **双引擎一行切换**：`AioSpiderEngine`（asyncio.Task + 信号量）vs `ScrapyEngine`（同步回调链 + ThreadPoolExecutor），`--engine both` 对比耗时
- **meta 传参惯用法**：列表页把 `rank/title` 放 `Request.meta`，详情页 `response.meta` 取回，避免二次解析列表页（Scrapy 惯用法）
- **dont_filter 重爬**：列表页每次运行都重爬（去重器按 URL 判重）
- **SQLite 零依赖存储**：stdlib `sqlite3` 写 `results.db`，`process_item` 逐条插入

### ScrapyEngine vs AioSpiderEngine

| 维度 | ScrapyEngine（`scrapy_sched`） | AioSpiderEngine（`aiospider_sched`） |
|------|-------------------------------|-------------------------------------|
| 并发模型 | 同步回调链 + `ThreadPoolExecutor` + requests | asyncio 协程 + 信号量 + aiohttp |
| 队列 | heapq 优先级队列 + RFPDupeFilter 去重 | asyncio.PriorityQueue + 域级限速 |
| 中间件 | 同步链 | 协程原生（支持 async 中间件） |
| 典型并发 | 16~64（线程受 GIL/线程数限制） | 100~1000（协程开销极小） |
| 适用场景 | 简单脚本、依赖 requests 生态、快速上手 | 高并发 IO 密集、长链接、大量小请求 |

**怎么选**：任务少、追求简单 → `ScrapyEngine`；目标站多、请求量大、并发要求高 → `AioSpiderEngine`。迁移成本为零（爬虫代码相同，仅换 Engine 一行）。

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 演示脚本（单文件）：mock 数据 + MockDownloaderMiddleware + HackerNewsSpider + 双引擎 runner |

## 运行方式

```bash
cd examples/scheduler_demo
python spider.py                        # 默认：AioSpiderEngine + 离线 mock 数据
python spider.py --engine scrapy        # ScrapyEngine（同步回调链 + 线程池）
python spider.py --engine both          # 同一爬虫两种引擎对比（输出耗时）
python spider.py --mode online          # 抓真实 Hacker News（需联网）
python spider.py --limit 10 --concurrent 20   # 控制详情页数量 / 并发
```

结果写入本地 `results.db`（SQLite，stdlib，零依赖）。

## 前置条件

- 离线模式零依赖（stdlib + bs4 + jimmyspider）
- 在线模式需联网（框架已含 `aiohttp` / `requests`）
- 无需 Redis / MongoDB / 登录

## 爬虫架构

```
main()  # --engine aio|scrapy|both × --mode offline|online × --limit × --concurrent
 ├─ aio:    AioSpiderEngine(spider=spider,
 │                          downloader_middlewares=[MockDownloaderMiddleware(mock)],
 │                          concurrent_requests=N, max_requests=1+limit).run()
 └─ scrapy: ScrapyEngine(同参数).run()   # --engine both 时两者各跑一遍对比耗时

HackerNewsSpider(BaseSpider)  name="hn_demo"
 ├─ start_requests() → Request(HN_LIST_URL, callback="parse_list", dont_filter=True)
 ├─ parse_list() → 每条标题 yield Request(callback="parse_detail", meta={rank, title})
 ├─ parse_detail() → yield {"title", "url", "points", "rank"}   # 引擎捕获 dict
 └─ process_item() → sqlite3 INSERT INTO items(title, url, points, rank)
```

数据流向：Request 队列 → 下载器（mock 短路或真实 HTTP）→ parse 回调链 → dict → process_item → SQLite `results.db`。引擎按 `max_requests` 限流，避免演示超跑；真实项目可去掉。

## 核心代码片段

**中间件短路下载**（离线模式核心，在线时返回 None 走真实网络）：

```python
class MockDownloaderMiddleware(DownloaderMiddleware):
    def process_request(self, request: Request):
        if not self.mock:
            return None                        # 在线模式走真实下载
        html = mock_html_for(request.url)
        if html is not None:
            return Response(url=request.url, status=200,
                            headers={"Content-Type": "text/html; charset=utf-8"},
                            body=html.encode("utf-8"), request=request)
        return None
```

**Scrapy 同构爬虫**（meta 传参 + 回调链）：

```python
def start_requests(self):
    yield Request(url=HN_LIST_URL, callback="parse_list", dont_filter=True)

def parse_list(self, response: Response):
    for rank, a in enumerate(soup.select("span.titleline > a")[:self.limit], start=1):
        yield Request(url=a.get("href"), callback="parse_detail",
                      meta={"rank": rank, "title": a.get_text(strip=True)})

def parse_detail(self, response: Response):
    score_el = soup.select_one("span.score")
    yield {"title": response.meta.get("title", ""), "url": response.url,
           "points": int(score_el.get_text().split()[0]) if score_el else 0,
           "rank": response.meta.get("rank", 0)}
```

**双引擎对比**（同一爬虫、同一并发、同一 mock 数据）：

```python
async def run_aio(spider: HackerNewsSpider, mock: bool, concurrent: int) -> dict:
    t0 = time.time()
    engine = AioSpiderEngine(spider=spider,
                             downloader_middlewares=[MockDownloaderMiddleware(mock)],
                             concurrent_requests=concurrent,
                             max_requests=1 + spider.limit)   # 列表页 + limit 个详情页
    await engine.run()
    return {"elapsed": time.time() - t0, "summary": spider.summary()}
```

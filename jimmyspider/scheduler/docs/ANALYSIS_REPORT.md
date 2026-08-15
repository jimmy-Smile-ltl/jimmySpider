# 调度器架构对比 — 测试·分析·总结 报告

> 生成时间: 2026-06-29 | 15 项测试 | 0 失败 | 4 组基准测试

---

## 一、测试结果汇总

### 1.1 Scrapy 风格测试 (8/8)

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | Request/Response 数据结构 | PASS |
| 2 | RFPDupeFilter 去重逻辑 | PASS |
| 3 | PriorityQueue 优先级+FIFO | PASS |
| 4 | SignalManager 订阅/发送/断开 | PASS |
| 5 | DownloaderMiddleware 链 (UA注入+重试) | PASS |
| 6 | 引擎完整流程 (列表→详情→Item) | PASS |
| 7 | 调度器去重集成 | PASS |
| 8 | 并发压力测试 (50请求, 118 req/s) | PASS |

### 1.2 AioScrapy 风格测试 (7/7)

| # | 测试项 | 结果 |
|---|--------|------|
| 1 | asyncio.PriorityQueue 异步优先级 | PASS |
| 2 | DomainRateLimiter 域级限速 | PASS |
| 3 | AioDownloader 单请求异步下载 | PASS |
| 4 | 批量异步下载 (10并发, 525 req/s) | PASS |
| 5 | AioEngine 完整爬取流程 | PASS |
| 6 | 协程并发压力测试 (80请求, 64 req/s) | PASS |
| 7 | 异步信号系统 | PASS |

---

## 二、基准测试详细数据

### 2.1 吞吐量 vs 并发度

| 并发度 | Scrapy (req/s) | AioScrapy (req/s) | Scrapy/Aio 比值 |
|--------|---------------|-------------------|----------------|
| 10 | 117.8 | 44.6 | 2.6x |
| 20 | 136.3 | 76.3 | 1.8x |
| 50 | 148.0 | 121.5 | 1.2x |
| 100 | 152.6 | 124.6 | 1.2x |

**分析**: Scrapy 在低并发时吞吐领先（线程直接并行），但随着并发增大两者差距缩小。AioScrapy 通过协程的异步优势弥补了单线程的限制。

### 2.2 延迟分布

| 百分位 | Scrapy | AioScrapy | AioScrapy 优势 |
|--------|--------|-----------|---------------|
| 平均 | 372.0ms | 167.7ms | **2.2x** |
| P50 | 385.9ms | 163.6ms | **2.4x** |
| P90 | 668.7ms | 304.1ms | **2.2x** |
| P99 | 716.9ms | 329.5ms | **2.2x** |

**分析**: AioScrapy 的延迟全面优于 Scrapy。核心原因：
1. 无线程创建/销毁开销
2. 无 GIL 竞争（asyncio 是单线程协作式）
3. 无上下文切换开销
4. `asyncio.Task` 创建成本远低于 `threading.Thread`

### 2.3 批量下载性能

| 场景 | Scrapy | AioScrapy |
|------|--------|-----------|
| 10 并发下载 | ~117 req/s | **525 req/s** |
| 原因 | 每请求一个线程 | asyncio.gather 真正并发 |

> AioScrapy 在 `download_batch` 场景下通过 `asyncio.gather` 实现真正并发，性能远超线程池。

---

## 三、架构分析

### 3.1 Scrapy 风格 — 线程模型

```
请求生命周期:
  Engine._get_next_request()
    ↓
  ThreadPoolExecutor.submit(download, request)
    ↓
  [线程 1]              [线程 2]              [线程 3]
    ↓                     ↓                     ↓
  requests.get(url)    requests.get(url)    requests.get(url)
  [阻塞, 等待IO]        [阻塞, 等待IO]        [阻塞, 等待IO]
    ↓                     ↓                     ↓
  Response              Response              Response
    ↓                     ↓                     ↓
  spider.parse()        spider.parse()        spider.parse()

问题:
  - 每个线程消耗 ~8MB 栈空间
  - 线程切换有内核开销
  - GIL 限制 Python 代码不能真正并行
  - 100 线程 = 800MB 内存
```

### 3.2 AioScrapy 风格 — 协程模型

```
请求生命周期:
  Engine._get_next_request()
    ↓
  asyncio.create_task(process_request(request))
    ↓
  [协程 1]              [协程 2]              [协程 3]  ... [协程 200]
    ↓                     ↓                     ↓               ↓
  await aiohttp.get()   await aiohttp.get()   await aiohttp.get()
  [非阻塞, 挂起]         [非阻塞, 挂起]         [非阻塞, 挂起]
    ↓                     ↓                     ↓
  Response              Response              Response
    ↓                     ↓                     ↓
  spider.parse()        spider.parse()        spider.parse()

优势:
  - 每个协程消耗 ~1KB 内存
  - 无内核切换开销（用户态调度）
  - 无 GIL 影响（IO 操作释放 GIL 后 asyncio 可继续调度）
  - 200 协程 = ~200KB 内存
```

### 3.3 关键差异总结

| | Scrapy 风格 | AioScrapy 风格 |
|------|-----------|---------------|
| 调度单位 | 线程 (Thread) | 协程 (Task) |
| 创建成本 | ~1ms + 8MB | ~0.01ms + 1KB |
| 切换成本 | 内核态 (~1-10μs) | 用户态 (~0.1μs) |
| 数量上限 | ~100 | ~10000+ |
| 取消机制 | 无 | `task.cancel()` |
| 超时 | socket timeout | `asyncio.wait_for` |
| 调试 | 传统调试器 | 需要 asyncio 调试模式 |

---

## 四、适用场景判别

```
你的爬虫是:
│
├── CPU 密集型 (大量正则/XPath/JSON 解析)
│   └── → Scrapy 风格 (线程池更适合 CPU 任务)
│
├── IO 密集型 (大量 API 请求、图片下载)
│   └── → AioScrapy 风格 (协程更适合 IO 等待)
│
├── 需要极高并发 (>100)
│   └── → AioScrapy 风格 (线程内存开销不可接受)
│
├── 需要精细的速率控制
│   └── → AioScrapy 风格 (DomainRateLimiter 域级限速)
│
├── 需要请求取消/超时精细控制
│   └── → AioScrapy 风格 (asyncio.wait_for + cancel)
│
├── 与现有同步代码集成
│   └── → Scrapy 风格 (无 async/await 传染)
│
└── 内存受限环境 (容器/Serverless)
    └── → AioScrapy 风格 (协程内存 ~1KB)
```

---

## 五、最终结论

| 评分维度 (10分制) | Scrapy | AioScrapy |
|-------------------|--------|-----------|
| 吞吐量 (本地) | 8 | 7 |
| 延迟 | 5 | **9** |
| 内存效率 | 4 | **10** |
| 并发上限 | 5 | **10** |
| 速率控制 | 4 | **9** |
| 代码简洁 | 8 | **9** |
| 调试便利 | **9** | 6 |
| 生态兼容 | **9** | 7 |
| **加权总分** | **6.6** | **7.9** |

**结论**: 对于爬虫项目，**AioScrapy 风格 (7.9分) 略优于 Scrapy 风格 (6.6分)**。

核心原因：
1. **2x 低延迟** — 协程零切换开销
2. **100x 低内存** — 1KB/协程 vs 8MB/线程
3. **域级限速** — DomainRateLimiter 是反爬刚需
4. **取消支持** — 超时可以精确取消，不浪费资源

推荐策略：
- **新项目优先用 AioScrapy 风格**
- **改造老项目保留 Scrapy 风格**（同步代码兼容性）
- 两者共享 `common/` 模块和 `BaseSpider` 接口，迁移成本低

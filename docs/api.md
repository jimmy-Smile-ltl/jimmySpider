# API 参考

## JimmySpider（基类）

所有爬虫的基类，初始化时自动装配全部组件。

```python
from jimmyspider import JimmySpider

class MySpider(JimmySpider):
    def run(self):
        ...
```

### 构造参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `pro_path` | `str` / `Path` | ✅ | 项目根目录路径，目录名自动作为 table_name |
| `test_url` | `str` | ❌ | 测试 URL，用于初始化代理检测 |
| `table_name` | `str` | ❌ | 自定义表名（默认取目录名） |

### 自动装配的组件

| 属性 | 类型 | 说明 |
|------|------|------|
| `self.log_print` | `LogPrint` | 日志实例 |
| `self.db_manager` | `HandleMongoDB` | MongoDB 操作 |
| `self.single_fetcher` | `SingleRequestHandler` | 同步请求器 |
| `self.async_fetcher` | `AsyncRequestHandler` | 异步请求器 |
| `self.thread_fetcher` | `ThreadRequestHandler` | 多线程请求器 |
| `self.file_saver` | `FileDownloader` | 文件下载器 |
| `self.html_saver` | `handleHTML` | HTML 清洗保存 |
| `self.extract_soup` | `extractSoup` | BS4 提取工具 |
| `self.table_name` | `str` | MongoDB collection / Redis 前缀 |
| `self.insert_num` | `int` | 已插入记录数 |

### 方法

#### `save_result(insert_list)`

保存数据到 MongoDB。支持单条（dict）或批量（list）。

```python
# 单条
self.save_result({"_id": "xxx", "title": "Hello"})

# 批量
self.save_result([{"_id": "1", ...}, {"_id": "2", ...}])
```

执行 upsert——存在则更新，不存在则插入。自动输出插入速率统计。

#### `format_duration(seconds) -> str`

将秒数转为易读的中文格式。

```python
self.format_duration(3661)  # → "1小时1分钟1秒"
```

---

## 请求处理器

### SingleRequestHandler

同步单线程请求，最基础的选择。

```python
from jimmyspider.request import SingleRequestHandler

fetcher = SingleRequestHandler(test_url="https://example.com")
res = fetcher.fetch("https://example.com/api")  # → {url: html}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `test_url` | `None` | 提供时启用代理轮换 |
| `use_clash_pool` | `False` | 是否使用 Clash 代理池 |

**`fetch(url, **kwargs) -> Optional[Dict[str, str]]`**

返回 `{url: response_text}` 或 `None`（重试耗尽时）。

内置代理轮换：每次请求前自动获取代理；遇到 403/反爬页面/速度过慢时自动切换代理重试（默认 5 次）。

### AsyncRequestHandler

aiohttp 异步协程，高并发场景。

```python
from jimmyspider.request import AsyncRequestHandler

fetcher = AsyncRequestHandler(max_workers=20, test_url="https://example.com")
results = fetcher.fetch_all(url_list)  # → {url: html, ...}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_workers` | `10` | 最大并发协程数 |
| `test_url` | `None` | 提供时启用代理 |
| `method` | `"GET"` | 默认请求方法 |

**`fetch_all(url_list, **kwargs) -> Dict[str, Optional[str]]`**

并发请求列表中所有 URL，返回结果字典。

### ThreadRequestHandler

线程池多线程，中等并发 + 线程上下文。

```python
from jimmyspider.request import ThreadRequestHandler

fetcher = ThreadRequestHandler(max_workers=10, test_url="...")
results = fetcher.fetch_all(url_list)
```

### CurlRequestHandler

curl_cffi 引擎，模拟浏览器 TLS 指纹（默认 chrome120）。

```python
from jimmyspider.request import CurlRequestHandler

fetcher = CurlRequestHandler(
    test_url="https://cf-protected.com",
    impersonate="chrome110"
)
res = fetcher.fetch(url)
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `impersonate` | `"chrome120"` | TLS 指纹伪装目标 |
| `test_url` | `None` | 代理检测 URL |

支持 impersonate 值：`chrome110`, `chrome120`, `chrome124`, `safari17_0`, `firefox120` 等。

### CurlCffiThreadRequestHandler

curl_cffi + ThreadPoolExecutor，指纹伪装 + 中等并发。

```python
fetcher = CurlCffiThreadRequestHandler(max_workers=10, test_url="...")
results = fetcher.fetch_all(url_list)
```

### CurlCffiAsyncRequestHandler

curl_cffi + asyncio，指纹伪装 + 最高并发。

```python
fetcher = CurlCffiAsyncRequestHandler(max_workers=20, test_url="...")
results = fetcher.fetch_all(url_list)
```

---

## Cache（Redis 缓存）

断点续爬和进度管理的核心。

```python
from jimmyspider import Cache

cache = Cache("my_project_page")
```

| 方法 | 说明 |
|------|------|
| `record_int(value)` | 记录整数（页码等） |
| `get_int(default=1)` | 读取整数 |
| `record_string(value)` | 记录字符串 |
| `get_string(default="")` | 读取字符串 |
| `record_list(value)` | 记录列表（覆盖） |
| `get_list()` | 读取列表 |
| `append_to_list(value)` | 追加到列表 |
| `remove_from_list(value)` | 从列表移除 |
| `get_list_length()` | 列表长度 |
| `clear_list(method='trim')` | 清空列表 |
| `add_to_set(value)` | 添加到集合 |
| `remove_from_set(value)` | 从集合移除 |
| `is_member_of_set(value)` | 检查成员 |
| `get_set_members()` | 获取所有成员 |
| `clear_value()` | 删除 key |
| `shutdown()` | 关闭连接 |

### 断点续爬惯用模式

```python
class Spider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.page_cache = Cache(f"{self.table_name}_log_page")
        self.error_pages = Cache(f"{self.table_name}_error_pages")
        self.finished = Cache(f"{self.table_name}_finished")
```

---

## HandleMongoDB（MongoDB 操作）

```python
from jimmyspider.mongo import HandleMongoDB

db = HandleMongoDB(table_name="my_collection")
```

| 方法 | 说明 |
|------|------|
| `insert_one(doc)` | 插入/更新单条（按 `_id` upsert） |
| `insert_many(docs)` | 批量插入（多线程并发） |
| `update_batch_in_bulk(updates)` | 批量更新（Bulk Write） |
| `update_batch_in_bulk_loop(updates)` | 批量更新（循环模式） |
| `deduplicate_by_last_id(lst)` | 按 `_id` 去重 |
| `count_by_filter(filter_dict)` | 计数 |
| `get_collection()` | 获取 pymongo Collection 对象 |

**注意**：每条记录必须有 `_id` 字段，框架使用 `generate_string_id(url)` 生成 URL 的 MD5 作为 `_id`。

---

## FileDownloader（文件下载器）

```python
from jimmyspider.file import FileDownloader

downloader = FileDownloader(
    pro_name="my_project",
    mode="thread",        # 'thread' 或 'async'
    max_workers=20,
    curl=False,           # True 时使用 curl_cffi
    test_url="https://...",
)
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `pro_name` | - | 项目名，决定存储路径 |
| `mode` | `"thread"` | `"thread"` 或 `"async"` |
| `max_workers` | `20` | 最大并发数 |
| `curl` | `False` | 是否使用 curl_cffi |
| `test_url` | `None` | 启用代理 |
| `default_type` | `".pdf"` | 默认文件扩展名 |
| `strict_mime_check` | `False` | 严格 MIME 检查 |

**`start(data_list)`** — 从数据列表中提取 `file_url_field` 字段下载。

文件保存路径：`{DATA_DIR}/{pro_name}/files_by_date/{YYYY-MM-DD}/{filename}`

---

## LogPrint（日志系统）

```python
from jimmyspider.log_print import LogPrint

logger = LogPrint(
    name="my_spider",
    log_dir="./logs",
    console_level=logging.INFO,
    file_level=logging.DEBUG,
)
```

| 方法 | 等价 logging |
|------|-------------|
| `logger.print(msg)` | `INFO` |
| `logger.info(msg)` | `INFO` |
| `logger.debug(msg)` | `DEBUG` |
| `logger.warning(msg)` | `WARNING` |
| `logger.error(msg)` | `ERROR` |
| `logger.critical(msg)` | `CRITICAL` |

日志文件自动按天轮转，保留 5 个历史文件；单文件最大 10MB，总大小超限自动清理。

---

## 工具函数

### `generate_string_id(text) -> str`

生成文本的 MD5，用作 MongoDB `_id`。

```python
from jimmyspider import generate_string_id

doc_id = generate_string_id("https://example.com/article/123")
```

### `generate_doi_id(doi) -> str`

标准化 DOI 后生成 MD5。

### `normalize_doi(doi) -> str`

移除 DOI 的 URL 前缀，统一为小写。

### `safe_extract_json(data, path, default="")`

安全地从嵌套 dict/list 中按路径提取值。

```python
safe_extract_json(data, ["result", 0, "title"])
# 等价于 data["result"][0]["title"]，但任何一步失败返回 default
```

### `rename_keys_by_mapping(d, mapping) -> dict`

根据映射表批量重命名键。

```python
rename_keys_by_mapping(
    {"old_name": "value"},
    {"old_name": "new_name"}
)
# → {"new_name": "value"}
```

### `convert_date_robust(date_str) -> str | None`

智能日期解析，支持绝对日期和相对时间。

```python
from jimmyspider.datetime_utils import convert_date_robust

convert_date_robust("November 15, 2024")  # → "2024-11-15 00:00:00"
convert_date_robust("3分钟前")             # → 当前时间-3分钟
```

---

## extractSoup（BS4 工具）

```python
from jimmyspider import extractSoup

soup = extractSoup()
```

| 方法 | 说明 |
|------|------|
| `extract_text(soup, selector)` | 提取单个文本 |
| `extract_texts(soup, selector)` | 提取多个文本 |
| `extract_href(soup, selector)` | 提取链接 |
| `extract_dict(soup, mapping)` | 按映射表提取字典 |
| `extract_list_url(soup, selector)` | 提取列表页 URL |
| `extract_media_urls(soup)` | 提取媒体 URL |
| `extract_pic_urls(soup)` | 提取图片 URL |
| `extract_content_recursively(soup)` | 递归提取内容 |
| `extract_tag_attrs(soup, tag, attr)` | 提取标签属性 |

---

## ProxyUtil（代理工具）

```python
from jimmyspider.proxy import ProxyUtil

proxy = ProxyUtil(test_url="https://example.com")
proxies = proxy.get_proxy()  # → {"http": "...", "https": "..."}
```

代理模式通过环境变量切换：

- 设置 `JIMMYSPIDER_PROXY_TUNNEL_URL` → 使用隧道代理
- 未设置 → `get_proxy_tunel()` 返回空 `{}`（直连）

---

## ClashManager（Clash 代理池）

```python
from jimmyspider.proxy_clash import ClashManager

clash = ClashManager({
    "api_url": "http://127.0.0.1:9097",
    "secret": "your-secret",
    "policy_group": "🚀 节点选择",
})

clash.start_auto_health_check(interval_sec=30)
proxy = clash.get_proxy_config()
clash.switch_to_healthy_node()
```

---

## Config（配置中心）

```python
from jimmyspider.config import get_config

cfg = get_config()
print(cfg.MONGO_URI)
print(cfg.DATA_DIR)
```

所有环境变量见 [配置指南](configuration.md)。

---

## RFPDupeFilter（请求去重器）

Scrapy RFPDupeFilter 风格的请求去重器。对每个请求计算 SHA1 指纹（url + method + body），已见过的请求被过滤。导出在顶层，定义在 `jimmyspider.request`。

```python
from jimmyspider import RFPDupeFilter

dupe = RFPDupeFilter(max_size=100000)
if not dupe.request_seen("https://example.com/page/1"):
    # 处理新请求
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_size` | `100000` | 去重集最大容量，满时自动清理一半防内存溢出 |

**`request_seen(url, method="GET", body=None) -> bool`**

已见过返回 `True`（应过滤），否则记录指纹并返回 `False`。除 URL 字符串外，也兼容带 `url`/`method`/`body` 属性的请求对象。

**`fingerprint(url, method="GET", body=None) -> str`** — 计算 SHA1 指纹。

**`clear()`** — 清空去重集。

---

## DomainRateLimiter（域级速率限制）

每个域名独立计时 + 锁的限速器，同步/异步版本共享同一计时表，互不影响。

```python
from jimmyspider import DomainRateLimiter

limiter = DomainRateLimiter(default_delay=1.0)

# 同步
limiter.wait(url)
resp = requests.get(url)

# 异步
await limiter.wait_async(url)
resp = await session.get(url)
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `default_delay` | `1.0` | 同一域名两次请求的最小间隔（秒） |

**`wait(url, delay=None)`** — 同步等待（`threading.Lock` 实现，线程安全），距上次同域名请求不足 `delay` 秒则休眠补齐。

**`wait_async(url, delay=None)`** — 异步等待（`asyncio.Lock` 实现，不阻塞事件循环）。

---

## 消息队列 jimmyspider.mq

三种消息队列（Redis / Kafka / RabbitMQ）的统一封装，专为爬虫任务分发设计。所有实现共享统一接口，切换 MQ 只需修改 import 语句。

### TaskMessage（统一消息体）

```python
from jimmyspider.mq import TaskMessage

task = TaskMessage(
    task_id="url_001",                  # 任务唯一标识
    task_type="crawl_detail",           # crawl_list / crawl_detail / download_file
    payload={"url": "https://..."},     # 任务负载
    priority=5,                         # 优先级 0-9，数字越大越优先
    max_retries=3,                      # 最大重试次数
)
task.to_json()             # 序列化为 JSON 字符串
TaskMessage.from_json(s)   # 反序列化
task.can_retry()           # 是否还可重试
task.increment_retry()     # 重试计数 +1
```

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `task_id` | `str` | - | 任务唯一标识 |
| `task_type` | `str` | - | `crawl_list` / `crawl_detail` / `download_file` |
| `payload` | `dict` | `{}` | 任务负载（URL、参数等） |
| `priority` | `int` | `0` | 优先级 0-9，越大越优先 |
| `max_retries` | `int` | `3` | 最大重试次数 |
| `retry_count` | `int` | `0` | 当前重试次数 |
| `created_at` | `float` | 当前时间 | 创建时间戳 |
| `metadata` | `dict` | `{}` | 额外元数据 |

### RedisProducer / RedisConsumer（默认实现）

```python
from jimmyspider.mq import RedisProducer, RedisConsumer

producer = RedisProducer(mode="stream")   # "list" 或 "stream"
producer.send("spider_tasks", task)

def handle(task: TaskMessage) -> bool:    # 返回 True = ACK，False = NACK 进重试链
    ...

consumer = RedisConsumer(mode="stream",
                         consumer_group="workers", consumer_name="worker_1")
consumer.consume("spider_tasks", handle)
```

- `RedisProducer(host, port, db, password, mode="list")` — `mode="list"`（LPUSH FIFO）或 `mode="stream"`（XADD + 消费者组）；连接参数默认读取全局配置（REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_PASSWORD）
- `RedisConsumer(host, port, db, password, mode="list", consumer_group, consumer_name, block_ms=5000, max_retries=3, dead_letter_suffix="_dead")` — `mode` 支持 `"list"` / `"stream"` / `"priority"`（ZSET 按优先级消费）；失败指数退避重试，超限进入 `{topic}_dead` 死信队列

### 其他实现（可选）

- `KafkaProducer(bootstrap_servers=[...])` — 分区并行、gzip/snappy/lz4 压缩、key 分区保序
- `RabbitMQProducer(host="localhost", port=5672, username, password, exchange_name, exchange_type)` — Exchange 灵活路由、消息持久化、TTL/死信延迟队列

完整文档见 [jimmyspider/mq/docs/message_queue.md](../jimmyspider/mq/docs/message_queue.md)。

---

## 调度引擎 jimmyspider.scheduler

Scrapy / AioScrapy 双风格调度器，均遵循五层架构（Engine → Scheduler → Downloader → Middleware → Spider）。

```python
from jimmyspider.scheduler import BaseSpider, Request, ScrapyEngine

class MySpider(BaseSpider):
    name = "my_spider"

    def start_requests(self):
        yield Request(url="https://example.com")

    def parse(self, response):
        yield {"title": response.text[:50]}

ScrapyEngine(spider=MySpider(), concurrent_requests=16).run()
```

| 组件 | 说明 |
|------|------|
| `Request` | 爬取请求：`url` / `method` / `headers` / `body` / `meta` / `callback` / `dont_filter` / `priority` |
| `Response` | 爬取响应：`url` / `status` / `text` / `body` / `request` / `meta`，附带 `xpath()` / `css()` |
| `BaseSpider` | 爬虫基类：实现 `start_requests()` 与 `parse()` 即可 |

**ScrapyEngine vs AioSpiderEngine**

| | `ScrapyEngine` | `AioSpiderEngine` |
|------|----------------|-------------------|
| 模型 | 同步回调链 + 线程池（requests） | asyncio 协程 + 信号量（aiohttp） |
| 调用 | `ScrapyEngine(spider=..., concurrent_requests=16).run()` | `asyncio.run(AioSpiderEngine(spider=..., concurrent_requests=100).run())` |
| 适用 | 兼容 Scrapy 习惯、中小并发 | 高并发场景，实测吞吐高约 2.4 倍 |

完整文档见 [jimmyspider/scheduler/docs/scheduler.md](../jimmyspider/scheduler/docs/scheduler.md)。

---

## 智能解析 jimmyspider.parser

5 层成本级联提取引擎：已知定位器 → 语义选择器 → JSON-LD/Meta → DOM 分析 → LLM 兜底。命中即返回，LLM 仅在低层全部失败时调用。基于 1035 个真实日报站点统计优化。

### TitleExtractor（中文标题提取器）

```python
from jimmyspider.parser import TitleExtractor

extractor = TitleExtractor()
result = extractor.extract(html, url="https://example.com/article/1")

result.value           # 标题文本
result.method          # selector / semantic / meta / dom / llm
result.confidence      # 置信度 0.0 ~ 1.0
result.selector_used   # 命中的定位器，如 "h1"
```

成功提取后自动按域名缓存定位器，同站点后续页面 0 token 成本复用。

### ContentExtractor（正文提取器）

```python
from jimmyspider.parser import ContentExtractor

result = ContentExtractor().extract(html, url)  # 返回 ExtractionResult
```

与 `TitleExtractor` 同签名：`extract(html, url="", known_selector="") -> ExtractionResult`。语义选择器 + 文本密度算法（Modified Readability）+ 云展网 `#ozoom` 特殊处理。

### SelectorCascade（级联引擎）

```python
from jimmyspider.parser import SelectorCascade

cascade = SelectorCascade(known_selectors={"title": "h1"})
page = cascade.extract(html, url="https://...", schema={
    "fields": [{"name": "title", "type": "text"}, {"name": "content", "type": "text"}],
    # llm_call=lambda html, field_name, field_def: "兜底结果"
})

page.selectors     # → 可复用的定位器字典 {"title": "h1"}
page.tokens_used   # → LLM token 消耗（0 = 未用 LLM）
```

- `known_selectors`: 已知定位器字典，命中成本为 0
- `field_thresholds`: 字段级置信度阈值，达到即停止降级（默认 0.80）
- `llm_call`: LLM 兜底钩子，接收 `(html, field_name, field_def)`，返回提取值

### ExtractionResult（提取结果）

| 字段 | 类型 | 说明 |
|------|------|------|
| `field_name` | `str` | 字段名 |
| `value` | `str` / `None` | 提取到的值，失败为 `None` |
| `selector_used` | `str` | 命中的定位器（如 `"h1"`、`"meta:og:title / jsonld"`、`"llm"`） |
| `method` | `str` | `selector` / `semantic` / `meta` / `dom` / `llm` |
| `confidence` | `float` | 置信度 0.0 ~ 1.0 |
| `candidates` | `list` | 所有候选值（调试用） |
| `latency_ms` | `float` | 提取耗时（毫秒） |
| `is_valid` | `bool`（属性） | `value` 非空时返回 `True` |

---

## 分布式 jimmyspider.distributed

多后端代理池、多数据库存储、监控告警三个子模块，均为异步接口，敏感配置统一来自全局配置。

### DistributedProxyManager（多后端代理池）

```python
from jimmyspider.distributed import DistributedProxyManager
from jimmyspider.distributed.proxy.backends import RedisPoolBackend, ClashPoolBackend

manager = DistributedProxyManager(strategy="weighted")  # primary / fallback / round_robin / weighted
manager.add_backend(RedisPoolBackend(...), priority=1, weight=10)
manager.add_backend(ClashPoolBackend(), priority=2, weight=5)

proxy = await manager.get_proxy(tags=["domestic"])   # 按策略取代理，后端故障自动降级
await manager.report_success(proxy)                  # 上报成功，恢复健康度
await manager.report_failure(proxy, error="403")     # 失败计数，连续 5 次自动摘除
```

- `add_backend(backend, priority=1, weight=10)` — `priority` 越小越优先；`weight` 用于加权随机策略（链式调用）
- 内置后端：`RedisPoolBackend`（Redis 代理池）、`ClashPoolBackend`（Clash 节点池）、`TunnelAPIBackend`（隧道代理 API）

### DistributedStorageManager（多库存储）

```python
from jimmyspider.distributed import DistributedStorageManager, MongoDBBackend, PostgreSQLBackend

storage = DistributedStorageManager(strategy="dual_write")  # 见下表
storage.set_primary(mongodb_backend)
storage.set_backup(pg_backend)
await storage.insert_one("reports", {"_id": "...", "title": "..."})
```

| 策略 | 行为 |
|------|------|
| `primary_only` | 只用主后端 |
| `dual_write` | 双写主 + 备份，读主库（备份失败不影响主） |
| `read_write_split` | 写主库、读从库 |
| `shard_by_collection` | 按 collection 分片到不同后端 |

接口：`insert_one` / `insert_many` / `upsert` / `find_one` / `find_many` / `bulk_write` / `update_one` / `update_many` / `delete_one` / `delete_many` / `count` / `aggregate` / `migrate_collection`（批量数据迁移）。

### 监控告警

- `MetricsCollector(namespace="spider", redis_url=...)` — Prometheus 格式指标采集：`incr()`（Counter）/ `set_gauge()`（Gauge）/ `observe()`（Histogram）/ `snapshot()`
- `HealthChecker(check_interval=30.0, failure_threshold=3)` — `register(name, check_func)` 注册检查项，healthy/degraded/unhealthy 状态机，连续失败触发告警
- `AlertManager()` — `add_rule(AlertRule(...))` 定义告警规则，`add_channel(...)` 接入企微/钉钉/飞书/Slack Webhook、Email、控制台

完整文档见 [distributed.md](distributed.md)。

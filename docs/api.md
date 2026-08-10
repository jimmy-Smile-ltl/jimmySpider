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

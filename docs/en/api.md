# API Reference

## JimmySpider (Base Class)

The base class for all spiders; it auto-assembles every component at initialization.

```python
from jimmyspider import JimmySpider

class MySpider(JimmySpider):
    def run(self):
        ...
```

### Constructor Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pro_path` | `str` / `Path` | ✅ | Project root directory path; the directory name is automatically used as the table_name |
| `test_url` | `str` | ❌ | Test URL used to initialize proxy detection |
| `table_name` | `str` | ❌ | Custom table name (defaults to the directory name) |

### Auto-Assembled Components

| Attribute | Type | Description |
|-----------|------|-------------|
| `self.log_print` | `LogPrint` | Logging instance |
| `self.db_manager` | `HandleMongoDB` | MongoDB operations |
| `self.single_fetcher` | `SingleRequestHandler` | Synchronous request handler |
| `self.async_fetcher` | `AsyncRequestHandler` | Async request handler |
| `self.thread_fetcher` | `ThreadRequestHandler` | Multithreaded request handler |
| `self.file_saver` | `FileDownloader` | File downloader |
| `self.html_saver` | `handleHTML` | HTML cleaning and saving |
| `self.extract_soup` | `extractSoup` | BS4 extraction utilities |
| `self.table_name` | `str` | MongoDB collection / Redis prefix |
| `self.insert_num` | `int` | Number of records inserted |

### Methods

#### `save_result(insert_list)`

Saves data to MongoDB. Supports a single record (dict) or a batch (list).

```python
# single record
self.save_result({"_id": "xxx", "title": "Hello"})

# batch
self.save_result([{"_id": "1", ...}, {"_id": "2", ...}])
```

Performs an upsert — updates if the record exists, inserts otherwise. Automatically prints insert-rate statistics.

#### `format_duration(seconds) -> str`

Converts seconds to a human-readable format.

```python
self.format_duration(3661)  # → "1小时1分钟1秒"
```

---

## Request Handlers

### SingleRequestHandler

Synchronous, single-threaded requests — the most basic choice.

```python
from jimmyspider.request import SingleRequestHandler

fetcher = SingleRequestHandler(test_url="https://example.com")
res = fetcher.fetch("https://example.com/api")  # → {url: html}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `test_url` | `None` | Enables proxy rotation when provided |
| `use_clash_pool` | `False` | Whether to use the Clash proxy pool |

**`fetch(url, **kwargs) -> Optional[Dict[str, str]]`**

Returns `{url: response_text}` or `None` (when all retries are exhausted).

Built-in proxy rotation: automatically fetches a proxy before each request; automatically switches proxy and retries (default 5 attempts) on 403, anti-bot pages, or excessively slow speeds.

### AsyncRequestHandler

aiohttp async coroutines for high-concurrency scenarios.

```python
from jimmyspider.request import AsyncRequestHandler

fetcher = AsyncRequestHandler(max_workers=20, test_url="https://example.com")
results = fetcher.fetch_all(url_list)  # → {url: html, ...}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_workers` | `10` | Maximum number of concurrent coroutines |
| `test_url` | `None` | Enables proxies when provided |
| `method` | `"GET"` | Default request method |

**`fetch_all(url_list, **kwargs) -> Dict[str, Optional[str]]`**

Concurrently requests all URLs in the list and returns the result dictionary.

### ThreadRequestHandler

Thread-pool multithreading for medium concurrency plus thread context.

```python
from jimmyspider.request import ThreadRequestHandler

fetcher = ThreadRequestHandler(max_workers=10, test_url="...")
results = fetcher.fetch_all(url_list)
```

### CurlRequestHandler

curl_cffi engine that mimics browser TLS fingerprints (default chrome120).

```python
from jimmyspider.request import CurlRequestHandler

fetcher = CurlRequestHandler(
    test_url="https://cf-protected.com",
    impersonate="chrome110"
)
res = fetcher.fetch(url)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `impersonate` | `"chrome120"` | TLS fingerprint impersonation target |
| `test_url` | `None` | Proxy detection URL |

Supported impersonate values: `chrome110`, `chrome120`, `chrome124`, `safari17_0`, `firefox120`, etc.

### CurlCffiThreadRequestHandler

curl_cffi + ThreadPoolExecutor: fingerprint impersonation with medium concurrency.

```python
fetcher = CurlCffiThreadRequestHandler(max_workers=10, test_url="...")
results = fetcher.fetch_all(url_list)
```

### CurlCffiAsyncRequestHandler

curl_cffi + asyncio: fingerprint impersonation with maximum concurrency.

```python
fetcher = CurlCffiAsyncRequestHandler(max_workers=20, test_url="...")
results = fetcher.fetch_all(url_list)
```

---

## Cache (Redis Cache)

The core of checkpoint/resume and progress management.

```python
from jimmyspider import Cache

cache = Cache("my_project_page")
```

| Method | Description |
|--------|-------------|
| `record_int(value)` | Records an integer (page numbers, etc.) |
| `get_int(default=1)` | Reads an integer |
| `record_string(value)` | Records a string |
| `get_string(default="")` | Reads a string |
| `record_list(value)` | Records a list (overwrites) |
| `get_list()` | Reads a list |
| `append_to_list(value)` | Appends to a list |
| `remove_from_list(value)` | Removes from a list |
| `get_list_length()` | List length |
| `clear_list(method='trim')` | Clears a list |
| `add_to_set(value)` | Adds to a set |
| `remove_from_set(value)` | Removes from a set |
| `is_member_of_set(value)` | Checks membership |
| `get_set_members()` | Gets all members |
| `clear_value()` | Deletes the key |
| `shutdown()` | Closes the connection |

### Common Checkpoint/Resume Pattern

```python
class Spider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.page_cache = Cache(f"{self.table_name}_log_page")
        self.error_pages = Cache(f"{self.table_name}_error_pages")
        self.finished = Cache(f"{self.table_name}_finished")
```

---

## HandleMongoDB (MongoDB Operations)

```python
from jimmyspider.mongo import HandleMongoDB

db = HandleMongoDB(table_name="my_collection")
```

| Method | Description |
|--------|-------------|
| `insert_one(doc)` | Inserts/updates a single record (upsert by `_id`) |
| `insert_many(docs)` | Batch insert (multithreaded) |
| `update_batch_in_bulk(updates)` | Batch update (Bulk Write) |
| `update_batch_in_bulk_loop(updates)` | Batch update (loop mode) |
| `deduplicate_by_last_id(lst)` | Deduplicates by `_id` |
| `count_by_filter(filter_dict)` | Count |
| `get_collection()` | Gets the pymongo Collection object |

**Note**: every record must have an `_id` field; the framework uses `generate_string_id(url)` to produce the MD5 of the URL as `_id`.

---

## FileDownloader (File Downloader)

```python
from jimmyspider.file import FileDownloader

downloader = FileDownloader(
    pro_name="my_project",
    mode="thread",        # 'thread' or 'async'
    max_workers=20,
    curl=False,           # uses curl_cffi when True
    test_url="https://...",
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pro_name` | - | Project name; determines the storage path |
| `mode` | `"thread"` | `"thread"` or `"async"` |
| `max_workers` | `20` | Maximum concurrency |
| `curl` | `False` | Whether to use curl_cffi |
| `test_url` | `None` | Enables proxy |
| `default_type` | `".pdf"` | Default file extension |
| `strict_mime_check` | `False` | Strict MIME type checking |

**`start(data_list)`** — extracts the `file_url_field` field from the data list and downloads.

Files are saved to: `{DATA_DIR}/{pro_name}/files_by_date/{YYYY-MM-DD}/{filename}`

---

## LogPrint (Logging System)

```python
from jimmyspider.log_print import LogPrint

logger = LogPrint(
    name="my_spider",
    log_dir="./logs",
    console_level=logging.INFO,
    file_level=logging.DEBUG,
)
```

| Method | Equivalent logging |
|--------|--------------------|
| `logger.print(msg)` | `INFO` |
| `logger.info(msg)` | `INFO` |
| `logger.debug(msg)` | `DEBUG` |
| `logger.warning(msg)` | `WARNING` |
| `logger.error(msg)` | `ERROR` |
| `logger.critical(msg)` | `CRITICAL` |

Log files rotate daily, keeping 5 historical files; each file has a 10MB maximum, and total-size overflow is cleaned up automatically.

---

## Utility Functions

### `generate_string_id(text) -> str`

Generates the MD5 of the given text for use as the MongoDB `_id`.

```python
from jimmyspider import generate_string_id

doc_id = generate_string_id("https://example.com/article/123")
```

### `generate_doi_id(doi) -> str`

Normalizes a DOI and generates its MD5.

### `normalize_doi(doi) -> str`

Removes the URL prefix from a DOI and lowercases it.

### `safe_extract_json(data, path, default="")`

Safely extracts a value from nested dicts/lists by path.

```python
safe_extract_json(data, ["result", 0, "title"])
# equivalent to data["result"][0]["title"], but returns default if any step fails
```

### `rename_keys_by_mapping(d, mapping) -> dict`

Batch-renames keys according to a mapping table.

```python
rename_keys_by_mapping(
    {"old_name": "value"},
    {"old_name": "new_name"}
)
# → {"new_name": "value"}
```

### `convert_date_robust(date_str) -> str | None`

Intelligent date parsing that supports absolute dates and relative time expressions.

```python
from jimmyspider.datetime_utils import convert_date_robust

convert_date_robust("November 15, 2024")  # → "2024-11-15 00:00:00"
convert_date_robust("3分钟前")             # → current time minus 3 minutes
```

---

## extractSoup (BS4 Utilities)

```python
from jimmyspider import extractSoup

soup = extractSoup()
```

| Method | Description |
|--------|-------------|
| `extract_text(soup, selector)` | Extracts a single text |
| `extract_texts(soup, selector)` | Extracts multiple texts |
| `extract_href(soup, selector)` | Extracts a link |
| `extract_dict(soup, mapping)` | Extracts a dict per a mapping table |
| `extract_list_url(soup, selector)` | Extracts list page URLs |
| `extract_media_urls(soup)` | Extracts media URLs |
| `extract_pic_urls(soup)` | Extracts image URLs |
| `extract_content_recursively(soup)` | Recursively extracts content |
| `extract_tag_attrs(soup, tag, attr)` | Extracts tag attributes |

---

## ProxyUtil (Proxy Utilities)

```python
from jimmyspider.proxy import ProxyUtil

proxy = ProxyUtil(test_url="https://example.com")
proxies = proxy.get_proxy()  # → {"http": "...", "https": "..."}
```

Proxy mode is switched via environment variables:

- Setting `JIMMYSPIDER_PROXY_TUNNEL_URL` → uses the tunnel proxy
- Not set → `get_proxy_tunel()` returns an empty `{}` (direct connection)

---

## ClashManager (Clash Proxy Pool)

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

## Config (Configuration Center)

```python
from jimmyspider.config import get_config

cfg = get_config()
print(cfg.MONGO_URI)
print(cfg.DATA_DIR)
```

All environment variables are listed in the [Configuration Guide](configuration.md).

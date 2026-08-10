# Framework Architecture

## Overview

jimmySpider is a layered spider framework built around the core idea of **base-class auto-assembly + swappable components**.

```
┌──────────────────────────────────────────────────────┐
│                    MySpider                          │
│           (inherits JimmySpider, writes run())       │
├──────────────────────────────────────────────────────┤
│                  JimmySpider                         │
│   ┌─────────┬──────────┬─────────┬──────────┐       │
│   │ MongoDB │  Redis   │ Logging │ HTML save│       │
│   │ storage │ resume   │ system  │ archive  │       │
│   └─────────┴──────────┴─────────┴──────────┘       │
│   ┌─────────┬──────────┬─────────┬──────────┐       │
│   │Request  │ File     │ Proxy   │ BS4      │       │
│   │handling │ download │ manager │ utilities│       │
│   │ (6)     │ (3)      │ (2)     │          │       │
│   └─────────┴──────────┴─────────┴──────────┘       │
├──────────────────────────────────────────────────────┤
│                    Config                            │
│            (env vars → config center)                │
├──────────────────────────────────────────────────────┤
│          MongoDB  |  Redis  |  Filesystem            │
│                  Infrastructure                      │
└──────────────────────────────────────────────────────┘
```

## Design Principles

### 1. Auto-assembly in the Base Class

`JimmySpider.__init__()` automatically initializes all components:

```python
class JimmySpider:
    def __init__(self, **kwargs):
        pro_name = Path(kwargs["pro_path"]).name
        self.table_name = pro_name           # directory name = all identifiers
        self.log_print = LogPrint(...)        # logging
        self.db_manager = HandleMongoDB(...)  # database
        self.single_fetcher = SingleRequestHandler(...)  # requests
        self.async_fetcher = AsyncRequestHandler(...)
        self.thread_fetcher = ThreadRequestHandler(...)
        self.file_saver = FileDownloader(...) # file download
        self.html_saver = handleHTML(...)     # HTML saving
        self.extract_soup = extractSoup()     # BS4 utilities
```

Subclasses only need to write the `run()` method; everything else works out of the box.

### 2. Naming as Configuration

The **directory name** runs through the entire framework:

| Context | Value | Description |
|---------|-------|-------------|
| `pro_path` | `Path(__file__).parent` | Project root directory |
| `table_name` | `pro_path.name` | MongoDB collection name |
| Redis key prefix | `{table_name}_xxx` | Checkpoint cache keys |
| File storage path | `{DATA_DIR}/{pro_name}/` | Directory for downloaded files |

Convention over configuration cuts down on boilerplate.

### 3. Swappable Components

All components can be overridden in subclasses:

```python
class MySpider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Replace with the curl_cffi handler
        from jimmyspider.request import CurlRequestHandler
        self.single_fetcher = CurlRequestHandler(
            test_url=self.test_url,
            impersonate="chrome120"
        )
```

### 4. Checkpoint/Resume Pattern

Every spider supports interruption recovery through Redis cache:

```
Start
  │
  ├─ finished_flag exists? ──yes──→ skip (already done)
  │
  └─ no → resume from log_page/log_date
            │
            crawl loop
            │
            ├─ save progress to Redis per page/item
            ├─ store failed URLs in error_pages
            │
            done → set finished_flag
```

## Data Flow

### Simple Spider

```
URL ──→ SingleRequestHandler.fetch() ──→ parse ──→ save_result() ──→ MongoDB
```

### Paginated Spider

```
page=1 ──→ fetch(list_url) ──→ parse list
                                  │
                   ┌──────────────┼──────────────┐
                   ▼              ▼              ▼
               detail_1      detail_2       detail_N
                   │              │              │
                   ▼              ▼              ▼
               save_result() save_result() save_result()
                   │              │              │
                   └──────────────┴──────────────┘
                                  │
                              page++
                              record_int(page)
```

### With File Downloads

```
fetch(list_url) ──→ parse list → [{url, file_url, ...}]
                                  │
                     save_result() │  file_saver.start()
                         ▼         │        ▼
                      MongoDB       └──→ spider_files/{pro}/
```

## Request Handler Selection Logic

```
Need TLS fingerprint impersonation?
├── yes → need high concurrency?
│   ├── yes → CurlCffiAsyncRequestHandler
│   └── no → CurlRequestHandler / CurlCffiThreadRequestHandler
└── no → need high concurrency?
    ├── yes → AsyncRequestHandler (aiohttp)
    └── no → SingleRequestHandler / ThreadRequestHandler
```

## Proxy Selection Logic

```
Have a Clash proxy pool?
├── yes → use_clash_pool=True → ClashManager health checks + auto switch
└── no → PROXY_TUNNEL_URL set?
    ├── yes → tunnel proxy
    └── no → direct connection
```

## Extension Points

### Adding a New Request Handler

1. Create a new class in `jimmyspider/request.py`
2. Implement `fetch(url, **kwargs)` or `fetch_all(url_list, **kwargs)`
3. Support the `test_url` parameter for proxy integration

### Adding a New Storage Backend

1. Follow the implementation in `jimmyspider/mongo.py`
2. Wire it up in `__init__` of `jimmyspider/spider.py`
3. Subclasses can override the `save_result()` method

### Adding a New Proxy Source

1. Follow `ProxyUtil` in `jimmyspider/proxy.py`
2. Implement `get_proxy()` and `test_proxy()`
3. Wire it into the request handler in `__init__`

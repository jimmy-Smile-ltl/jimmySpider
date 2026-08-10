# jimmySpider

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![中文](https://img.shields.io/badge/README-中文-red.svg)](README.md)

A mature, flexible Python web scraping framework, battle-tested across **140+ websites**.

[中文文档](README_zh.md)

## ✨ Features

- 🚀 **6 Request Handlers** — Sync / Thread / Async + curl_cffi TLS fingerprinting
- 💾 **MongoDB Storage** — Auto upsert, batch insert, deduplication
- 🔄 **Redis Checkpoint Resume** — Page/date/error URL cache, auto-resume on restart
- 🌐 **Proxy Management** — Tunnel proxy + Clash multi-node pool (health check / auto-switch)
- 📁 **File Downloader** — Thread / Async / curl_cffi modes
- 📝 **Logging System** — Console + daily rotating file logs
- 🧹 **HTML Clean & Archive** — Date-based HTML storage
- 📅 **Smart Date Parsing** — Absolute & relative time parsing
- 🛡️ **Anti-Bot Arsenal** — Cloudflare / JsJiami / RS / AWS WAF countermeasures

## 📦 Installation

```bash
pip install jimmyspider
```

Or from source:

```bash
git clone https://github.com/jimmysmile/jimmySpider.git
cd jimmySpider
pip install -e .
```

### Required Services

MongoDB and Redis are required:

```bash
mongod --dbpath /data/db --fork --logpath /var/log/mongodb.log
redis-server
```

## 🚀 Quick Start

```python
from pathlib import Path
from jimmyspider import JimmySpider, Cache

class MySpider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.page_cache = Cache(f"{self.table_name}_page")
        self.finished = Cache(f"{self.table_name}_finished")

    def run(self):
        if self.finished.get_string():
            print("Already finished, skipping")
            return

        page = self.page_cache.get_int(default=1)
        while True:
            url = f"https://example.com/api?page={page}"
            res = self.single_fetcher.fetch(url)
            if not res:
                break
            # process data...
            self.page_cache.record_int(page)
            page += 1

        self.finished.record_string("done")

if __name__ == "__main__":
    MySpider(pro_path=Path(__file__).parent).run()
```

## ⚙️ Configuration

**Recommended: YAML config file.** Copy the template to get started:

```bash
cp jimmyspider.yaml.example jimmyspider.yaml
```

```yaml
# jimmyspider.yaml
mongo_uri: "mongodb://localhost:27017/"
mongo_db: "jimmyspider"
redis_host: "127.0.0.1"
redis_port: 6379
data_dir: "~/spider_files"
# proxy (optional)
proxy_tunnel_url: "http://user:pass@proxy:15818"
```

**Priority**: Environment variables > YAML config > defaults

The config file is auto-detected from current directory, home directory (`~/.jimmyspider.yaml`), or `JIMMYSPIDER_CONFIG_FILE` env var.

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_MONGO_URI` | `mongodb://localhost:27017/` | MongoDB URI |
| `JIMMYSPIDER_MONGO_DB` | `jimmyspider` | MongoDB database |
| `JIMMYSPIDER_REDIS_HOST` | `127.0.0.1` | Redis host |
| `JIMMYSPIDER_REDIS_PORT` | `6379` | Redis port |
| `JIMMYSPIDER_REDIS_PASSWORD` | - | Redis password |
| `JIMMYSPIDER_DATA_DIR` | `~/spider_files` | File download directory |
| `JIMMYSPIDER_PROXY_TUNNEL_URL` | - | Tunnel proxy URL |
| `JIMMYSPIDER_CLASH_API_URL` | `http://127.0.0.1:9097` | Clash API URL |
| `JIMMYSPIDER_CLASH_SECRET` | - | Clash API secret |

> Full config guide: [docs/configuration.md](docs/configuration.md)

## 📚 Request Handler Selection

| Scenario | Handler | Notes |
|----------|---------|-------|
| Simple sites | `SingleRequestHandler` | Synchronous, sequential |
| High concurrency | `AsyncRequestHandler` | aiohttp async |
| Mixed IO | `ThreadRequestHandler` | Thread pool |
| Cloudflare / TLS | `CurlRequestHandler` | curl_cffi fingerprinting |
| Fast downloads | `CurlCffiThreadRequestHandler` | curl_cffi + threads |
| Fast async | `CurlCffiAsyncRequestHandler` | curl_cffi + asyncio |

## 📂 Examples

See `examples/` for 9 real-world spider projects.

## 🏗️ Project Conventions

```
my_spider/
├── spider.py          # Main spider
├── spider_list.py     # List page (optional)
├── spider_detail.py   # Detail page (optional)
└── logs/              # Auto-created
```

- **Directory name = MongoDB collection = Redis key prefix**
- **MongoDB `_id`**: `generate_string_id(url)` — MD5 of URL

## 📄 License

MIT License — see [LICENSE](LICENSE)

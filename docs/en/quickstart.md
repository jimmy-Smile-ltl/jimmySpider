# Quick Start

## Requirements

- Python 3.10+
- MongoDB (data storage)
- Redis (checkpoint/resume cache)

## Installation

```bash
# Install from PyPI
pip install jimmyspider

# Or install from source
git clone https://github.com/jimmysmile/jimmySpider.git
cd jimmySpider
pip install -e .
```

## Write a Spider in 5 Minutes

### 1. Create the project directory

```
my_spider/
└── spider.py
```

### 2. Write the spider

```python
import os
from pathlib import Path
from jimmyspider import JimmySpider, Cache, generate_string_id

class Spider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Checkpoint/resume: track the current page number
        self.page_cache = Cache(f"{self.table_name}_page")
        # Completion flag
        self.finished = Cache(f"{self.table_name}_finished")

    def run(self):
        # Skip if already finished
        if self.finished.get_string():
            self.log_print.info("Task already completed, skipping")
            return

        # Resume the page number from the checkpoint
        page = self.page_cache.get_int(default=1)

        while True:
            url = f"https://example.com/api/articles?page={page}"
            self.log_print.info(f"Crawling page {page}...")

            res = self.single_fetcher.fetch(url)
            if not res:
                self.log_print.warning(f"Request failed for page {page}, stopping")
                break

            data = res.get(url)
            if not data or len(data) == 0:
                self.log_print.info("No more data, done")
                break

            # Save to MongoDB
            items = []
            for item in data:
                item["_id"] = generate_string_id(item["url"])
                items.append(item)

            self.save_result(items)

            # Record progress
            self.page_cache.record_int(page)
            page += 1

        # Mark as finished
        self.finished.record_string("done")
        self.log_print.info("Crawling complete!")

if __name__ == "__main__":
    pro_path = Path(__file__).parent
    Spider(pro_path=pro_path).run()
```

### 3. Run it

```bash
python spider.py

# After a Ctrl+C interruption, running again resumes from the checkpoint
```

## Framework Conventions

### The Directory Name Is Everything

```
pro_my_site/                  # directory name
├── spider.py
└── logs/
```

- The **directory name** `pro_my_site` = MongoDB collection name = Redis key prefix
- JimmySpider automatically initializes all components based on the directory name

### MongoDB

- Each record's `_id` is generated with `generate_string_id(url)` (the MD5 of the URL)
- `save_result()` upserts automatically (updates if it exists, inserts if it doesn't)

### Redis Checkpoint/Resume

It's recommended to create the following cache keys for each spider:

```python
self.page_cache = Cache(f"{self.table_name}_log_page")       # page number
self.date_cache = Cache(f"{self.table_name}_log_date")       # date range
self.error_pages = Cache(f"{self.table_name}_error_pages")   # failed URLs
self.finished_flag = Cache(f"{self.table_name}_finished")    # completion flag
```

### Logging

`self.log_print.print()` / `.info()` / `.warning()` / `.error()` automatically output to both the console and log files.

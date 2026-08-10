# 快速开始

## 环境要求

- Python 3.10+
- MongoDB（数据存储）
- Redis（断点续爬缓存）

## 安装

```bash
git clone https://github.com/jimmy-Smile-ltl/jimmySpider.git
cd jimmySpider
pip install -e .
```

## 5 分钟写一个爬虫

### 1. 创建项目目录

```
my_spider/
└── spider.py
```

### 2. 编写爬虫

```python
import os
from pathlib import Path
from jimmyspider import JimmySpider, Cache, generate_string_id

class Spider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 断点续爬：记录当前页码
        self.page_cache = Cache(f"{self.table_name}_page")
        # 完成标记
        self.finished = Cache(f"{self.table_name}_finished")

    def run(self):
        # 如果已完成，跳过
        if self.finished.get_string():
            self.log_print.info("任务已完成，跳过")
            return

        # 从断点恢复页码
        page = self.page_cache.get_int(default=1)

        while True:
            url = f"https://example.com/api/articles?page={page}"
            self.log_print.info(f"正在爬取第 {page} 页...")

            res = self.single_fetcher.fetch(url)
            if not res:
                self.log_print.warning(f"第 {page} 页请求失败，停止")
                break

            data = res.get(url)
            if not data or len(data) == 0:
                self.log_print.info("没有更多数据，结束")
                break

            # 保存到 MongoDB
            items = []
            for item in data:
                item["_id"] = generate_string_id(item["url"])
                items.append(item)

            self.save_result(items)

            # 记录进度
            self.page_cache.record_int(page)
            page += 1

        # 标记完成
        self.finished.record_string("done")
        self.log_print.info("爬取完成！")

if __name__ == "__main__":
    pro_path = Path(__file__).parent
    Spider(pro_path=pro_path).run()
```

### 3. 运行

```bash
python spider.py

# Ctrl+C 中断后，再次运行会从断点继续
```

## 框架约定

### 目录名即一切

```
pro_my_site/                  # 目录名
├── spider.py
└── logs/
```

- **目录名** `pro_my_site` = MongoDB collection 名 = Redis key 前缀
- JimmySpider 会自动根据目录名初始化所有组件

### MongoDB

- 每条记录的 `_id` 使用 `generate_string_id(url)` 生成（URL 的 MD5）
- `save_result()` 自动 upsert（存在则更新，不存在则插入）

### Redis 断点续爬

推荐为每个爬虫创建以下缓存键：

```python
self.page_cache = Cache(f"{self.table_name}_log_page")       # 页码
self.date_cache = Cache(f"{self.table_name}_log_date")       # 日期范围
self.error_pages = Cache(f"{self.table_name}_error_pages")   # 失败 URL
self.finished_flag = Cache(f"{self.table_name}_finished")    # 完成标记
```

### 日志

`self.log_print.print()` / `.info()` / `.warning()` / `.error()` 自动输出到控制台和文件。

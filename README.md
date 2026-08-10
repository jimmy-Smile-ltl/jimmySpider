# jimmySpider

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![English](https://img.shields.io/badge/README-English-blue.svg)](README_en.md)

一个成熟、灵活的 Python 爬虫框架，从 **140+ 网站实战** 中提炼而来。

## 目录

- [特性](#-特性)
- [安装](#-安装)
- [5 分钟上手](#-5-分钟上手)
- [核心概念](#-核心概念)
- [配置](#-配置)
- [请求处理器](#-请求处理器)
- [断点续爬](#-断点续爬)
- [反爬策略](#-反爬策略)
- [示例项目](#-示例项目)
- [项目结构约定](#-项目结构约定)
- [框架架构](#-框架架构)
- [文档导航](#-文档导航)
- [贡献](#-贡献)

## ✨ 特性

- 🚀 **6 种请求处理器** — 单线程 / 多线程 / 异步 + curl_cffi TLS 指纹伪装（chrome110/120/124, safari, firefox）
- 💾 **MongoDB 存储层** — 自动 upsert、批量写入（多线程并发）、去重
- 🔄 **Redis 断点续爬** — 页码 / 日期 / 错误 URL 缓存，Ctrl+C 中断后自动恢复
- 🌐 **代理管理** — 快代理隧道 + Clash 多节点代理池（健康检测 / 自动切换 / 黑名单）
- 📁 **文件下载器** — 多线程 / 异步 / curl_cffi 三种模式，MIME 检测 + 智能重试
- 📝 **日志系统** — 控制台 + 按天轮转文件日志（自动清理超出大小限制的旧日志）
- 🧹 **HTML 清洗与归档** — 移除 style/link/注释，按日期保存
- 📅 **日期智能解析** — 支持绝对时间（各种格式）+ 相对时间（"3分钟前"、"昨天"）
- 🛡️ **反爬对抗** — Cloudflare / 加速乐 CDN / 瑞数 / AWS WAF 等多种反爬方案的内置支持

## 📦 安装

### 前提条件

- Python 3.10+
- MongoDB（数据存储）
- Redis（断点续爬缓存）

### pip 安装

```bash
pip install jimmyspider
```

### 从源码安装

```bash
git clone https://github.com/jimmysmile/jimmySpider.git
cd jimmySpider
pip install -e .
```

### 启动依赖服务

```bash
# MongoDB
mongod --dbpath /data/db --fork --logpath /var/log/mongodb.log

# Redis
redis-server
```

## 🚀 5 分钟上手

### 1. 创建项目

```
mkdir my_spider
cd my_spider
```

### 2. 编写 spider.py

```python
"""我的第一个 jimmySpider 爬虫 — 抓取 Hacker News 首页"""
from pathlib import Path
from jimmyspider import JimmySpider, Cache, generate_string_id

class Spider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 断点续爬缓冲
        self.page = Cache(f"{self.table_name}_page")
        self.finished = Cache(f"{self.table_name}_finished")

    def run(self):
        # 已完成则跳过
        if self.finished.get_string():
            self.log_print.info("✅ 任务已完成")
            return

        page_num = self.page.get_int(default=1)

        while True:
            url = f"https://news.ycombinator.com/?p={page_num}"
            self.log_print.info(f"📄 抓取第 {page_num} 页: {url}")

            res = self.single_fetcher.fetch(url)
            if not res:
                self.log_print.warning(f"第 {page_num} 页请求失败，停止")
                break

            html = res[url]
            if not html:
                break

            # 解析标题
            items = []
            soup = self.extract_soup
            # ... 解析 HTML ...

            if not items:
                self.log_print.info("没有更多数据，结束")
                break

            # 保存到 MongoDB
            self.save_result(items)

            # 记录进度
            self.page.record_int(page_num)
            page_num += 1

        # 标记完成
        self.finished.record_string("done")
        self.log_print.info("🎉 爬取完成！")

if __name__ == "__main__":
    Spider(pro_path=Path(__file__).parent).run()
```

### 3. 运行

```bash
python spider.py
# Ctrl+C 中断后，再次运行会从断点继续
```

## 💡 核心概念

### 1. 目录名即一切

jimmySpider 遵循"约定大于配置"原则：

```
pro_my_site/           ← 这个目录名贯穿整个框架
├── spider.py
└── logs/
```

| 上下文 | 自动取值 | 说明 |
|--------|---------|------|
| MongoDB Collection | `pro_my_site` | 数据存入同名集合 |
| Redis Key 前缀 | `pro_my_site_xxx` | 断点缓存键 |
| 文件存储路径 | `{DATA_DIR}/pro_my_site/` | 下载文件目录 |
| 日志文件名 | `pro_my_site.log` | 日志文件 |

你只需要命名好目录，框架自动处理其余部分。

### 2. 基类自动装配

`JimmySpider` 在 `__init__` 中自动实例化所有组件，你的子类只需写 `run()`。

### 3. Component 可替换

所有组件都可以在子类中覆盖：

```python
class MySpider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 替换为 curl_cffi 处理器对抗 Cloudflare
        from jimmyspider.request import CurlRequestHandler
        self.single_fetcher = CurlRequestHandler(
            test_url=self.test_url,
            impersonate="chrome120"
        )
```

## ⚙️ 配置

**推荐使用 YAML 配置文件**，复制模板即可：

```bash
cp jimmyspider.yaml.example jimmyspider.yaml
```

```yaml
# jimmyspider.yaml — 所有配置集中管理
mongo_uri: "mongodb://localhost:27017/"
mongo_db: "jimmyspider"
redis_host: "127.0.0.1"
redis_port: 6379
data_dir: "~/spider_files"
# 隧道代理（可选）
proxy_tunnel_url: "http://user:pass@proxy:15818"
```

**配置优先级**：环境变量 > YAML 文件 > 默认值

配置文件自动从当前目录、用户目录或 `JIMMYSPIDER_CONFIG_FILE` 环境变量加载。

> 完整配置项和 Docker 示例见 [docs/configuration.md](docs/configuration.md)

## 📡 请求处理器

框架提供 6 种请求处理器，覆盖从简单爬取到高级反爬的全场景：

| 处理器 | 引擎 | 并发模型 | TLS 伪装 | 适用场景 |
|--------|------|---------|---------|---------|
| `SingleRequestHandler` | requests | 同步 | ❌ | 简单网站、调试 |
| `AsyncRequestHandler` | aiohttp | asyncio | ❌ | 高并发 API |
| `ThreadRequestHandler` | requests | 线程池 | ❌ | 中等并发 |
| `CurlRequestHandler` | curl_cffi | 同步 | ✅ | Cloudflare 站点 |
| `CurlCffiThreadRequestHandler` | curl_cffi | 线程池 | ✅ | CF + 并发下载 |
| `CurlCffiAsyncRequestHandler` | curl_cffi | asyncio | ✅ | CF + 高并发 |

**如何选择？**

```
需要 TLS 指纹伪装（Cloudflare/403 错误）？
├── 是 → 选 CurlXxx 系列
│   ├── 需要高并发？ → CurlCffiAsyncRequestHandler
│   ├── 需要中等并发？ → CurlCffiThreadRequestHandler
│   └── 简单场景 → CurlRequestHandler
└── 否 → 选标准系列
    ├── 大量 URL 并发？ → AsyncRequestHandler
    ├── 需要线程上下文？ → ThreadRequestHandler
    └── 简单场景 → SingleRequestHandler
```

> 详细选择指南见 [docs/request_handlers.md](docs/request_handlers.md)

## 🔄 断点续爬

框架通过 Redis 实现中断恢复，是爬虫长期稳定运行的核心机制：

```
首次运行:  启动 → 从 page=1 开始 → 每页保存进度 → 完成 → 设置 finished 标记
中断重启:  启动 → 检查 finished（无）→ 读取上次 page → 从该页继续 → 完成
再次运行:  启动 → 检查 finished（有）→ "已完成，跳过"
```

标准模式：

```python
class Spider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.page_cache = Cache(f"{self.table_name}_log_page")    # 当前位置
        self.date_cache = Cache(f"{self.table_name}_log_date")    # 日期进度
        self.error_pages = Cache(f"{self.table_name}_error_pages") # 失败 URL
        self.finished = Cache(f"{self.table_name}_finished")      # 完成标记
```

## 🛡️ 反爬策略

| 现象 | 根因 | 解决方案 |
|------|------|----------|
| **403 Forbidden** | Cloudflare 防护 | 切换 `CurlRequestHandler`，设置 `impersonate="chrome120"` |
| **521 错误** | 加速乐 CDN | 双层 JS 挑战，需 Cookie hook + execjs |
| **412 错误** | 瑞数 WAF | JSVMP 保护，需 CDP 级诊断 |
| **空响应** | TLS 指纹不匹配 | 切换 curl_cffi，尝试不同 impersonate 值 |
| **验证码** | 滑块/点选 | Playwright + stealth.js 自动化 |
| **10000 条截断** | API 限制 | 按天/年/类别分片搜索 |
| **99 页限制** | 前端防爬 | 按月拆分时间范围 |

## 📂 示例项目

`examples/` 目录包含 **10 个实战爬虫示例**：

| 示例 | 来源站点 | 展示特性 |
|------|---------|---------|
| `eastmoney_report/` | 东方财富 | 研报下载、分类翻页、Redis 断点 |
| `state_council_policy/` | 国务院 | list+detail 分页、HTML 解析 |
| `moj_regulations/` | 司法部 | JSON POST、字段映射、API 采集 |
| `medlive_guide/` | 医脉通 | 分类树遍历、医学数据 |
| `cicc_report/` | 中金研报 | TLS 指纹、加速乐 Cookie |
| `unicamp_br/` | 巴西大学 | 多策略采集（学科/年份/补充） |
| `cuni_cz/` | 查理大学 | list+detail、多线程 |
| `escholarship_org/` | eScholarship | curl_cffi、AWS WAF |
| `pubmed_ncbi/` | PubMed | 日期分片、多阶段流水线 |
| `oatd/` | OATD 学位论文 | AsyncRequestHandler、Cookie 刷新、代理轮换 |

每个示例都有详细的模块文档字符串。完整说明见 [docs/examples.md](docs/examples.md)。

## 🏗️ 项目结构约定

```
my_spider_project/
├── spider.py          # 主爬虫：class Spider(JimmySpider)
├── spider_list.py     # 列表页爬虫（可选）
├── spider_detail.py   # 详情页爬虫（可选）
├── logs/              # 自动创建的日志目录
```

**命名约定：**
- 目录名 = MongoDB collection 名 = Redis key 前缀
- MongoDB `_id`：`generate_string_id(url)` — URL 的 MD5
- 类名：`Spider(JimmySpider)`，详情页 `SpiderDetail(JimmySpider)`
- 入口：`if __name__ == "__main__": Spider(pro_path=Path(__file__).parent).run()`

**数据流约定：**

```
列表页 → [{...}, {...}] → save_result() → MongoDB
                           │
                           └→ file_saver.start() → 本地文件
```

## 🏛️ 框架架构

```
┌────────────────────────────────────────────┐
│              你的爬虫                        │
│       class Spider(JimmySpider)             │
│       def run(self): ...                    │
├────────────────────────────────────────────┤
│             JimmySpider 基类                │
│  ┌──────┬──────┬──────┬──────┬──────┐      │
│  │Mongo │Redis │请求器│文件  │日志  │      │
│  │存储  │缓存  │(6种) │下载  │系统  │      │
│  └──────┴──────┴──────┴──────┴──────┘      │
├────────────────────────────────────────────┤
│              Config 配置层                   │
│        (环境变量 → 统一配置)                 │
├────────────────────────────────────────────┤
│      MongoDB    │   Redis   │   文件系统     │
└────────────────────────────────────────────┘
```

> 详细架构见 [docs/architecture.md](docs/architecture.md)

## 📚 文档导航

| 文档 | 内容 |
|------|------|
| [docs/quickstart.md](docs/quickstart.md) | 快速开始，从零写一个爬虫 |
| [docs/api.md](docs/api.md) | 完整 API 参考（所有类和方法） |
| [docs/architecture.md](docs/architecture.md) | 框架架构、设计原则、数据流 |
| [docs/configuration.md](docs/configuration.md) | 所有环境变量详解 |
| [docs/request_handlers.md](docs/request_handlers.md) | 请求处理器选择指南 |
| [docs/proxy.md](docs/proxy.md) | 代理配置指南（隧道 + Clash） |
| [docs/examples.md](docs/examples.md) | 10 个示例项目详解 |

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。

## 📄 协议

MIT License — 详见 [LICENSE](LICENSE)

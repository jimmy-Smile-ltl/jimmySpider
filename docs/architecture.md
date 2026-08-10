# 框架架构

## 概览

jimmySpider 是一个分层的爬虫框架，核心思想是 **基类装配 + 组件可替换**。

```
┌──────────────────────────────────────────────────────┐
│                    MySpider                          │
│           (继承 JimmySpider, 写 run())               │
├──────────────────────────────────────────────────────┤
│                  JimmySpider                         │
│   ┌─────────┬──────────┬─────────┬──────────┐       │
│   │ MongoDB │  Redis   │ 日志     │ HTML保存  │       │
│   │ 存储    │ 断点续爬  │ 系统    │ 归档      │       │
│   └─────────┴──────────┴─────────┴──────────┘       │
│   ┌─────────┬──────────┬─────────┬──────────┐       │
│   │ 请求处理 │ 文件下载  │ 代理管理 │ BS4工具  │       │
│   │ (6种)   │ (3种)    │ (2种)   │          │       │
│   └─────────┴──────────┴─────────┴──────────┘       │
├──────────────────────────────────────────────────────┤
│                    Config                            │
│            (环境变量 → 配置中心)                      │
├──────────────────────────────────────────────────────┤
│           MongoDB  |  Redis  |  文件系统              │
│                  基础设施                             │
└──────────────────────────────────────────────────────┘
```

## 设计原则

### 1. 基类自动装配

`JimmySpider.__init__()` 自动初始化所有组件：

```python
class JimmySpider:
    def __init__(self, **kwargs):
        pro_name = Path(kwargs["pro_path"]).name
        self.table_name = pro_name           # 目录名 = 所有标识
        self.log_print = LogPrint(...)        # 日志
        self.db_manager = HandleMongoDB(...)  # 数据库
        self.single_fetcher = SingleRequestHandler(...)  # 请求
        self.async_fetcher = AsyncRequestHandler(...)
        self.thread_fetcher = ThreadRequestHandler(...)
        self.file_saver = FileDownloader(...) # 文件下载
        self.html_saver = handleHTML(...)     # HTML保存
        self.extract_soup = extractSoup()     # BS4工具
```

子类只需写 `run()` 方法，其他开箱即用。

### 2. 命名即配置

**目录名** 贯穿整个框架：

| 上下文 | 值 | 说明 |
|--------|-----|------|
| `pro_path` | `Path(__file__).parent` | 项目根目录 |
| `table_name` | `pro_path.name` | MongoDB collection 名 |
| Redis key 前缀 | `{table_name}_xxx` | 断点缓存键 |
| 文件存储路径 | `{DATA_DIR}/{pro_name}/` | 下载文件目录 |

约定大于配置，减少重复代码。

### 3. 组件可替换

所有组件都可以在子类中覆盖：

```python
class MySpider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 替换为 curl_cffi 处理器
        from jimmyspider.request import CurlRequestHandler
        self.single_fetcher = CurlRequestHandler(
            test_url=self.test_url,
            impersonate="chrome120"
        )
```

### 4. 断点续爬模式

每个爬虫通过 Redis 缓存实现中断恢复：

```
启动
  │
  ├─ finished_flag 存在？ ──是──→ 跳过（已完成）
  │
  └─ 否 → 从 log_page/log_date 恢复进度
            │
            循环爬取
            │
            ├─ 每页/每条保存进度到 Redis
            ├─ 失败的 URL 存入 error_pages
            │
            完成 → 设置 finished_flag
```

## 数据流

### 简单爬虫

```
URL ──→ SingleRequestHandler.fetch() ──→ 解析 ──→ save_result() ──→ MongoDB
```

### 分页爬虫

```
page=1 ──→ fetch(list_url) ──→ 解析列表
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

### 带文件下载

```
fetch(list_url) ──→ 解析列表 → [{url, file_url, ...}]
                                  │
                     save_result() │  file_saver.start()
                         ▼         │        ▼
                      MongoDB       └──→ spider_files/{pro}/
```

## 请求处理器选择逻辑

```
需要 TLS 指纹伪装？
├── 是 → 需要高并发？
│   ├── 是 → CurlCffiAsyncRequestHandler
│   └── 否 → CurlRequestHandler / CurlCffiThreadRequestHandler
└── 否 → 需要高并发？
    ├── 是 → AsyncRequestHandler (aiohttp)
    └── 否 → SingleRequestHandler / ThreadRequestHandler
```

## 代理选择逻辑

```
有 Clash 代理池？
├── 是 → use_clash_pool=True → ClashManager 健康检测 + 自动切换
└── 否 → PROXY_TUNNEL_URL 设置？
    ├── 是 → 隧道代理
    └── 否 → 直连
```

## 扩展点

### 添加新的请求处理器

1. 在 `jimmyspider/request.py` 中新建类
2. 实现 `fetch(url, **kwargs)` 或 `fetch_all(url_list, **kwargs)`
3. 支持 `test_url` 参数集成代理

### 添加新的存储后端

1. 参考 `jimmyspider/mongo.py` 实现
2. 在 `jimmyspider/spider.py` 的 `__init__` 中装配
3. 子类可以覆盖 `save_result()` 方法

### 添加新的代理源

1. 参考 `jimmyspider/proxy.py` 中的 `ProxyUtil`
2. 实现 `get_proxy()` 和 `test_proxy()`
3. 在 `__init__` 中装配到请求处理器

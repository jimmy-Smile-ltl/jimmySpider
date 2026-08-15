# hello_postgresql — PostgreSQL 数据库入门示例

以 Hacker News 首页为抓取对象，演示用 jimmyspider 框架 + PostgreSQL 后端跑通「请求 → 解析 → 落库 → 断点」完整流程。与 `hello_world` 的爬虫逻辑完全一致，唯一区别是通过 `db_type="postgresql"` 把数据库后端切换为 PostgreSQL。

## 站点

- 站点页面：https://news.ycombinator.com/ — Hacker News 技术新闻社区，经典 HTML 分页（`?p=N`）
- 抓取范围：仅前 2 页（演示用，改 `while page <= 2` 即可抓全站）
- 数据内容：新闻标题（`span.titleline > a` 的文本）+ 原文链接（同选择器的 href）

## 展示特性

- **数据库后端切换**：`kwargs.setdefault("db_type", "postgresql")` — 与 `hello_mysql` 仅此一行不同，演示框架多数据库支持
- **自动建表 + 批量 upsert**：`save_result(items)` 自动路由到 `PostgreSQLHandler.insert_data_list`，表自动建于 `public` schema（可配置 `pg_schema`），结构为 `_unique_key`（唯一键）+ `data`（JSONB）+ 时间戳
- **插入/更新精准区分**：upsert 使用 `ON CONFLICT ... RETURNING (xmax=0) AS inserted`，能区分本次是新增还是更新
- **URL MD5 主键**：`generate_string_id(link)` 将 URL 取 MD5 作为 `_unique_key`，重复运行自动 upsert
- **Redis 断点续爬**：页码缓存 `hello_postgresql_page` + 完成标记 `hello_postgresql_finished`，中断后从断点页码继续，完成后直接跳过
- **extractSoup 批量解析**：`extract_texts` / `extract_list_url` 一条 CSS 选择器同时取文本与链接
- **连通性自检**：`test_url` 交给基类在初始化时探活
- **日志即进度**：每页抓取、入库、完成均有 `log_print` 输出

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件，约 60 行） |

## 运行方式

```bash
cd examples/hello_postgresql
python spider.py
```

## 前置条件

- `pip install psycopg2-binary`
- 创建数据库：`createdb jimmyspider`（库名可在 `jimmyspider.yaml` 的 `pg_db` 修改）
- **Redis**：断点缓存（`hello_postgresql_page` 页码、`hello_postgresql_finished` 完成标记）
- 无需登录与代理，直连即可

## 爬虫架构

```
run()
 ├─ 检查 finished 标记 → 已完成后直接跳过
 ├─ 读取断点页码（缺省 1），循环 page <= 2
 │   ├─ single_fetcher.fetch(f"https://news.ycombinator.com/?p={page}")
 │   ├─ extract_texts / extract_list_url 解析标题与链接（span.titleline > a）
 │   ├─ 组装 items：{_unique_key: generate_string_id(link), url, title}
 │   └─ save_result(items) → PostgreSQLHandler.insert_data_list（自动建表 + upsert）
 │       同时 record_int(page) 写页码断点
 └─ record_string("done") 写完成标记
```

数据流向：HTML → 标题/链接二元组 → PostgreSQL（`_unique_key` 防重）；Redis 记录页码与完成态。

## 核心代码片段

**数据库后端切换 + 断点初始化**：

```python
kwargs.setdefault("db_type", "postgresql")  # 切换数据库后端的唯一开关
kwargs.setdefault("test_url", "https://news.ycombinator.com/")
super().__init__(**kwargs)
self.page = Cache(f"{self.table_name}_page")        # 页码断点
self.finished = Cache(f"{self.table_name}_finished")  # 完成标记
```

**抓取 → 解析 → 落库主循环**：

```python
res = self.single_fetcher.fetch(url)
if not res:
    break
html = res[url]
titles = self.extract_soup.extract_texts(html, "span.titleline > a")
urls = self.extract_soup.extract_list_url(html, "span.titleline > a")
items = []
for title, link in zip(titles, urls):
    items.append({
        "_unique_key": generate_string_id(link),  # URL MD5 防重
        "url": link,
        "title": title,
    })
if not items:
    break
self.save_result(items)   # 自动调用 PostgreSQLHandler.insert_data_list
self.page.record_int(page)
page += 1
```

**完成标记（幂等运行的关键）**：

```python
def run(self):
    if self.finished.get_string():        # 已跑完 → 直接跳过
        self.log_print.info("已完成，跳过")
        return
    ...
    self.finished.record_string("done")   # 全部页码抓完 → 写完成标记
```

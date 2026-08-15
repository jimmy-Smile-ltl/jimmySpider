# arXiv 论文爬虫 (arxiv_org)

两个互补的 arXiv 爬虫写入同一张 PostgreSQL 表 `article_arxiv_org`：`spider_arxiv_new.py` 抓当天 cs.AI 新增论文，`spider_cs_all.py` 用高级搜索按 10 天时间窗分片抓 2020 年至今的全部 CS 论文。迁移自盛大网络 pro1_arxiv_org。

## 站点

- 站点：https://arxiv.org — 全球最大预印本平台，反爬基本无，用 curl_cffi 伪装 Chrome TLS 指纹即可稳定抓取
- 列表页 A（新增）：`https://arxiv.org/list/cs.AI/new?skip={偏移}&show=50`，每页 50 篇
- 列表页 B（全量）：`https://arxiv.org/search/advanced`（高级搜索，`date-from_date` / `date-to_date` 指定时间窗）
- 详情页：摘要页 `/abs/{id}`，解析 `#abs` 区块的标题/摘要/作者/分类/日期/DOI

## 展示特性

- **curl_cffi TLS 指纹伪装**：列表请求走 `CurlRequestHandler`（impersonate Chrome），详情并发走 `ThreadRequestHandler(max_workers=3)`
- **PostgreSQL 批量 upsert**：`insert_data_list(page_articles, unique_col="article_url")`，重复抓取自动更新
- **Redis 日期级断点**：`spider_arxiv_new` 用 `log_page_article_arxiv_org_new` + `log_date_article_arxiv_org_new`，跨天自动重置进度、同一天重复运行直接跳过
- **10 天分片策略**（`spider_cs_all`）：搜索单次最多返回 10,000 条（50 条/页 × 200 页），把时间范围切成 10 天一个窗口逐段爬取，永不超限
- **窗口断点**（`spider_cs_all`）：`log_date_cs_all_article_arxiv_org` 记录窗口起点（默认 2020-01-01），`log_page_cs_all_article_arxiv_org` 记录窗口内页码，中断后按窗口续爬
- **结构化详情解析**：标题/摘要去掉 `span.descriptor` 前缀后提取，作者以 `{作者主页 href: 姓名}` 字典入 JSONB，分类按 `;` 拆分为列表
- **文件链接全量留存**：列表页每个条目的所有链接（abstract/html/pdf 等）以 `file_info` JSONB 保存
- **表数据过少自动重建**：`delete_table_if_less = 20`，首次运行自动建表（`SERIAL` 主键 + `JSONB` 字段 + `article_url` 唯一键）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_arxiv_new.py` | 抓 cs.AI 每日新增：`/list/cs.AI/new` 翻页 + 摘要页详情解析，页间 sleep 30 秒 |
| `spider_cs_all.py` | 抓全量 CS 论文：高级搜索 + 10 天时间窗分片 + 翻页，与前者同表 |

## 运行方式

```bash
cd examples/arxiv_org
python spider_arxiv_new.py   # 当天 cs.AI 新增论文
python spider_cs_all.py      # 2020 年至今全部 CS 论文（按 10 天窗口推进）
```

## 前置条件

- **PostgreSQL**：`db_type="postgresql"`，连接参数在 `jimmyspider.yaml` 或环境变量中配置
- **Redis**：断点缓存（key 前缀 `log_page_` / `log_date_` + `article_arxiv_org` + `_new` / `cs_all_`）
- `pip install curl_cffi psycopg2-binary`
- 第二阶段（可选）：按标题去 Google Scholar 补作者信息，见 `examples/google_scholar/get_author_by_title.py`

## 爬虫架构

```
spider_arxiv_new.py（cs.AI 每日新增）
 ├─ 日期检查：无记录 → 首次运行清页码；新的一天 → 清页码重爬；同一天 → 跳过
 ├─ while True:
 │   ├─ get_page：list/cs.AI/new?skip=(p-1)*50&show=50
 │   ├─ 解析 #articles > dt 条目 → {file_info: 链接字典, article_url}
 │   ├─ ThreadRequestHandler 并发抓摘要页 → 标题/摘要/作者/分类/日期/DOI
 │   ├─ insert_data_list(unique_col="article_url")
 │   ├─ 不足 50 条或无下一页 → 清页码 + 记录当天日期，结束
 │   └─ 翻页 sleep 30 秒

spider_cs_all.py（全量 CS）
 ├─ current_date = log_date 窗口起点（默认 2020-01-01）
 ├─ while 窗口起点 <= 今天:
 │   ├─ 窗口 = [start_date, start_date + 10 天]
 │   ├─ handle_ten_days：高级搜索翻页（classification-computer_science=y,
 │   │   date-filter_by=date_range, order=announced_date_first, size=50, start=偏移）
 │   │   → 解析 ol.breathe-horizontal li.arxiv-result → 并发抓详情 → upsert
 │   └─ 窗口完成 → record_string 新窗口起点
```

两条数据流都汇聚到 PostgreSQL 表 `article_arxiv_org`（`article_url` 唯一键），重复运行自动更新已有论文。

## 核心代码片段

**高级搜索 10 天窗口参数**（分片反爬的核心）：

```python
params = {
    "advanced": "",
    "classification-computer_science": "y",
    "classification-physics_archives": "all",
    "classification-include_cross_list": "include",
    "date-filter_by": "date_range",
    "date-from_date": start_date,          # 窗口起点
    "date-to_date": end_date,              # 窗口终点（+10 天）
    "date-date_type": "submitted_date",
    "abstracts": "show",
    "order": "announced_date_first",
    "size": f"{self.page_size}",           # 50
    "start": f"{(page_num - 1) * self.page_size}",
}
```

**摘要页详情解析**（两个爬虫共用同一套逻辑）：

```python
title_tag = article_soup.select_one("#abs h1.title")
title_tag.select_one("span.descriptor").decompose()   # 去掉 "Title:" 前缀
author_list = {tag.attrs.get("href"): tag.get_text().strip()
               for tag in article_soup.select("#abs div.authors a")}
date_match = re.search(r"on\s+(.+?)\]", date_tag.get_text().strip())
```

**跨天自动重置进度**（`spider_arxiv_new`）：

```python
if now_date > start_date:
    self.log_print.print(f"新的一天 {now_date}，清除进度重新开始")
    self.log_page.clear_value()      # 跨天 → 页码清零重爬
else:
    self.log_print.print("时间记录异常，跳过本次运行")
    return                            # 同一天已完成 → 直接跳过
```

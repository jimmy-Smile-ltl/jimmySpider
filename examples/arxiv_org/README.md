# arXiv 论文爬虫 (arxiv_org)

从盛大网络 `pro1_arxiv_org` 迁移。两个爬虫写入同一张 PostgreSQL 表 `article_arxiv_org`，互为补充。

## 站点

- 站点：https://arxiv.org — 全球最大预印本平台
- 反爬：基本无；用 curl_cffi 伪装 Chrome 指纹即可稳定抓取

## 展示特性

- **PostgreSQL 批量 upsert**：`insert_data_list(..., unique_col="article_url")`，重复抓取自动更新
- **curl_cffi TLS 指纹伪装**（`CurlRequestHandler`）+ 3 线程并发抓详情（`ThreadRequestHandler`）
- **Redis 断点**：页码 + 日期；`spider_arxiv_new` 跨天自动重置进度，`spider_cs_all` 按窗口续爬
- **10 天分片策略**（`spider_cs_all`）：高级搜索单次最多返回 10,000 条，按时间窗切片绕过上限
- 表数据过少自动重建（首次运行场景），建表 SQL 交给 handler 自动补 schema

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_arxiv_new.py` | 抓 cs.AI 每日新增：list 页翻页 + 摘要页详情解析（标题/摘要/作者/分类/日期/DOI） |
| `spider_cs_all.py` | 抓全量 CS 论文：高级搜索 + 10 天时间窗分片 + 翻页，与前者同表 |

## 运行方式

```bash
cd examples/arxiv_org
python spider_arxiv_new.py   # 当天 cs.AI 新增论文
python spider_cs_all.py      # 2020 年至今全部 CS 论文（按 10 天窗口推进）
```

## 前置条件

- PostgreSQL（`db_type="postgresql"`，连接参数在 `jimmyspider.yaml` 或环境变量中配置）
- Redis（断点缓存）；`pip install curl_cffi psycopg2-binary`
- 第二阶段（可选）：按标题去 Google Scholar 补作者信息，见 `examples/google_scholar/get_author_by_title.py`

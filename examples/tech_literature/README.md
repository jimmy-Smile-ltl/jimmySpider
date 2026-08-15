# Frontiers 科技文献抓取 (tech_literature)

从期刊列表出发，逐期刊分页调用文章检索 API，再并发抓取每篇文章详情页，解析正文/关键词/作者/审稿人/编辑/参考文献/通讯作者等结构化信息后写入 MySQL。迁移自北大信研院 pro18「科技文献 Frontiers」，去掉了 HDFS 上传。

## 站点

- 站点页面：https://www.frontiersin.org/journals — Frontiers 出版集团学术期刊，约 288 本（含合作伙伴期刊）
- 期刊列表接口：`https://www.frontiersin.org/api/v3/journals/search/journal-filter`（POST JSON，每次 Top=16）
- 文章检索接口：`/api/v3/journals/search/articles`，按重定向后的期刊域名自动选择 `frontiersin.org` / `frontierspartnerships.org` / `ebm-journal.org` 的 API 基址
- 详情页：文章 `publicUrl`，正文在 `div.JournalFullText div.JournalFullText`（或 `#fulltext`）

## 展示特性

- **三级抓取架构**：期刊列表 → 单期刊分页文章列表 → 文章详情页（`async_fetcher.fetch_all` 并发）
- **四键期刊级断点**：当前期刊 `log_journal_tech_literature`、期刊内页码 `log_page_tech_literature`、已抓数量 `log_finish_num_tech_literature`、已完成期刊列表 `log_finished_tech_literature`，中断后原样恢复
- **多域名 API 自动适配**：访问期刊主页跟随重定向，按域名选择文章 API 基址，期刊 ID 从「提交文章」链接的 `entityid` 参数解析
- **PDF-only 文章兼容**：`isArticleArchive` 标记的文章无正文页，直接以列表链接作为 PDF 链接
- **结构化文献字段**：正文、摘要、关键词（`Keywords:` 标记后文本节点）、作者、审稿人（`Reviewed by:`）、编辑（`Edited by:`）、通讯作者（`*Correspondence:`，Base64 邮箱自动解码）、参考文献（`p.ReferencesCopy1`，兼容第二种结构）、接收/录用/发布日期（`#timestamps` 或 `div.metadatekey` 双结构）、PDF/EPUB 下载链接
- **期刊信息本地缓存**：首次抓取写入 `journal_info.json`，后续运行免重复请求
- **测试数据自清理**：表内残留数据（<100 条）自动清表并重置全部断点

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件） |
| `journal_info.json` | 期刊信息缓存（首次运行自动生成，约 288 条） |

## 运行方式

```bash
cd examples/tech_literature
python spider.py
```

## 前置条件

- **MySQL**：`db_type="mysql"`，连接参数见根目录 `jimmyspider.yaml`
- **Redis**：断点缓存（key 前缀 `tech_literature_`，共 4 个）
- 无需登录与代理，直连即可

## 爬虫架构

```
run()
 ├─ 残留测试数据（<100 条）→ clear_table + 重置断点
 ├─ get_journal_infos()：journal_info.json 存在则读缓存，否则分页调 journal-filter 接口并落盘
 ├─ for journal_info（跳过 log_finished 中已完成的期刊）:
 │   └─ handle_journal(journal_info)
 │       ├─ 断点恢复：上次中断的期刊从 log_page 继续，否则从第 0 页
 │       ├─ get_api_url_journal_id：访问期刊主页，按重定向域名选 API 基址 + 解析 journal_id
 │       ├─ while True: POST 文章检索（Skip/Top=16/时间范围 1920~今天/Filter）
 │       │   ├─ parse_journal_page → 文章基础信息（标题/DOI/摘要/作者/日期/领域）
 │       │   ├─ fetch_articles：async 并发抓详情，补正文/关键词/参考文献/审稿人/编辑/通讯作者/PDF
 │       │   ├─ save_result（article_url 唯一键 upsert）
 │       │   └─ 连续报错超限或已抓完 → 返回成功
 │       └─ 成功 → 期刊名入 log_finished，重置期刊级断点
```

数据流向：期刊接口 → 期刊列表 → 文章检索接口 → 文章列表 → 详情页 HTML → 20 个结构化字段 → MySQL 单表（`tech_literature`）。

## 核心代码片段

**期刊检索接口调用**（`fetch_journal_infos`）：

```python
url = "https://www.frontiersin.org/api/v3/journals/search/journal-filter"
data = json.dumps({
    "Skip": 16 * page, "Top": 16, "DomainId": 0,
    "JournalIds": [], "Search": "", "FirstLetter": "",
}, separators=(",", ":"))
response = self.single_fetcher.fetch(url, headers=self.headers, data=data, method="POST")
journal_info_list.append({
    "journal_name": journal.get("AlternativeText", ""),
    "journal_url": journal.get("PublicUrl", ""),
    "journal_id": journal.get("Id", ""),
    "article_count": journal.get("ArticleCount", {}).get("Count", 0),
    "ISSN": journal.get("ISSN", ""),
})
```

**按重定向域名选择 API 基址**（`get_api_url_journal_id`）：

```python
if "frontierspartnerships.org" in journal_res.url:
    return "https://www.frontierspartnerships.org/api/v3/journals/search/articles", journal_id, all_num
if "frontiersin.org" in journal_res.url:
    return api_url_self, journal_id, all_num
if ".ebm-journal.org" in journal_res.url:
    return "https://www.ebm-journal.org/api/v3/journals/search/articles", journal_id, all_num
parsed = urlparse(journal_res.url)
return f"{parsed.scheme}://{parsed.netloc}/api/v3/journals/search/articles", "", all_num
```

**通讯作者 Base64 邮箱解码**：

```python
email = ""
base64_email = link.get_text(strip=True)
if base64_email:
    try:
        email = base64.b64decode(base64_email).decode("utf-8")
    except Exception:
        email = ""
```

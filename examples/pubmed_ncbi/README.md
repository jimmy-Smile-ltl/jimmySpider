# PubMed 文献抓取管线 (pubmed_ncbi)

## 站点

- 站点页面：https://pubmed.ncbi.nlm.nih.gov — NCBI PubMed 生物医学文献库，收录超 3 千万篇生物医学文献
- 搜索限制：**单个搜索条件最多返回 10,000 条 / 1,000 页** — 按年数据量超过两万条时必须细化搜索条件
- 全文 PDF：link.springer.com（Springer 出版社，`content/pdf/{doi}.pdf` 直链）
- 反爬：Cloudflare 式 "checking your browser" 插页（curl_cffi 可绕过 TLS 指纹检测）；详情页请求缺 `referer` 时返回精简版 HTML（无作者机构信息）

## 展示特性

- **按天压缩时间范围**（列表页核心策略）：把搜索时间范围压缩到单天（`"YYYY/MM/DD"[Date - Create]`），从今天倒序步行到 2000-01-01，每天一个查询 — 任何单次查询都远低于 10,000 条上限
- **天内多线程翻页**：每天内 `ThreadPoolExecutor`（10 线程）并发抓所有页；每页标记 done，失败页重试
- **动态总页数提取**：依次尝试 `data-pages-amount`/`data-max-page`（取较小值）、`label.of-total-pages`（"of 23,680" 文本）、`data-last-page` 三种来源，全部失败则回退 Redis 缓存或默认 50 页；超过 1000 页上限时截断并告警
- **单篇论文特殊处理**：某天只有 1 篇时响应直接跳到详情页（URL 无 `?`），单独走「当天仅一篇」分支入库
- **多层断点**：已完成日期集合、每日期页面完成集合、错误页集合、每日文章数统计（`daily_count` JSON 映射），重跑秒跳已完成日期
- **curl_cffi TLS 指纹绕过**：列表页与详情页均用 `curl_cffi`（详情页 `impersonate="chrome120"`），"checking your browser" 插页靠 sleep 重试等待自动通过
- **详情页结构化解析**：作者 + 机构关联（`affiliation-link` 编号对应）、摘要分段（`strong.sub-title` 小节）、**DOI 三重备选**（`span.identifier.doi` → `meta[name=citation_doi]` → `span.citation-doi`）、基金资助、版权声明
- **浏览器取 cookie + 自动刷新**：详情页用 DrissionPage（`get_cookies_by_url`）获取有效 cookie，连续批量失败时自动强制刷新
- **共享并发更新引擎**：`update_batch_in_bulk_loop` 批量拉库 + 8 线程并发 + Redis `last_id` 断点
- **多阶段完整管线**：列表 → 详情 → JSONL 清洗 → Springer PDF 下载，四种文件对应四个阶段（含两个无库轻量工具）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_list.py` | 列表爬虫：按天倒序分片 + 10 线程并发分页，写 MongoDB，Redis 多层断点 |
| `spider_detail.py` | 详情爬虫：读库并发抓详情，作者/摘要/DOI/基金等结构化解析回写 |
| `spider_50_to_jsonl.py` | 无库轻量变体：固定搜索条件抓前 50 页（约 500 条），合并列表+详情写 JSONL，输出原始/清洗/Springer 过滤三个文件 |
| `springer_down_txt_xm.py` | Springer PDF 批量下载器：从 TXT 读 DOI 列表，10 线程下载，批次目录 + 断点续传 |

## 运行方式

```bash
cd examples/pubmed_ncbi
python spider_list.py              # 阶段1：按天分片抓列表（自动从今天倒序到 2000-01-01）
python spider_detail.py            # 阶段2：读库抓详情补全字段
python spider_50_to_jsonl.py       # 阶段3(可选)：50页轻量抓取 → pubmed_500.jsonl / _clean.jsonl / springer.jsonl
python springer_down_txt_xm.py     # 阶段4(可选)：按 DOI 列表下载 Springer PDF（默认读 doi.jsonl）
```

## 前置条件

- 依赖服务（列表/详情两爬虫）：
  - **Redis**：已完成日期集合（`pro31_pubmed_ncbi_list_completed_dates`）、每日期页完成/错误/总页数缓存、每日文章数（`..._list_daily_count`）、详情断点（`..._detail_last_id`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `pubmed_ncbi`（代码中 `table_name = pro31_pubmed_ncbi`），按 `_id` upsert
- `spider_50_to_jsonl.py` 无外部依赖（不连 Redis/MongoDB）
- `springer_down_txt_xm.py` 需先准备 DOI 列表文件（默认 `doi.jsonl`，每行一个 DOI），且**原脚本硬编码的 Springer 会话 cookies 已移除，运行前需从浏览器登录抓取新的会话 cookies 填入全局 `cookies` 字典**
- `spider_detail.py` 依赖 DrissionPage（浏览器取 cookie）；两个 curl_cffi 脚本需 `pip install curl_cffi`
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

**多阶段管线**：

```
阶段1 spider_list.py（按天分片）
 generate_date_ranges(今天 → 2000-01-01)     # 每天一个查询，突破 10000 条上限
  └─ 每天: 第1页探 TOTAL_PAGES（动态提取→Redis→默认50）
      ├─ ThreadPoolExecutor(10) 并发剩余页
      │   ├─ curl_cffi + "checking your browser" 重试
      │   └─ 成功→MongoDB+标记页done；失败→错误集
      ├─ 错误页重试 → daily_count 记录 → 标记日期完成
 └─ 汇总报告（完成/跳过/部分/失败天数 + 最近有文章 Top10）

阶段2 spider_detail.py（读库补全）
 update_batch_in_bulk_loop(filter={detail_url存在, detail_parsed!=true}, 8线程, last_id断点)
  └─ 每文档: curl_cffi(chrome120) 抓详情 → 解析作者/摘要/DOI/基金 → 回写 + _doi_id

阶段3 spider_50_to_jsonl.py（无库）
 50页列表 → 逐条抓详情（referer 必须带列表页 URL）→ merge_article（详情优先+列表独有字段）
 → pubmed_500.jsonl（原始）→ pubmed_500_clean.jsonl（去 detail_url）→ springer.jsonl（DOI 前缀 10.1007/10.1186/10.1038）

阶段4 springer_down_txt_xm.py（PDF 下载）
 doi.jsonl → 按前缀拼 URL（10.1186/10.1038 走 _reference.pdf）→ 10线程下载（%PDF 魔数校验）
 → 时间戳批次目录（每批 ≤10 文件）→ download_success.txt / download_failed.jsonl 断点续传
```

数据流向：搜索页 HTML → 文章条目（标题/作者/期刊引用/PMID/摘要片段）→ MongoDB；详情页 HTML → 结构化字段 → 回写；或合并写 JSONL → Springer 过滤 → DOI 列表 → PDF 文件。

## 核心代码片段

**按天生成搜索范围**（每天一查，绕开单查询上限）：

```python
def generate_date_ranges(start_date_str, end_date_str="2000-01-01"):
    start = datetime.strptime(start_date_str, "%Y-%m-%d")
    end = datetime.strptime(end_date_str, "%Y-%m-%d")
    current = start
    while current >= end:
        yield current.strftime("%Y/%m/%d")
        current -= timedelta(days=1)

def build_term(date_str):
    return (f'(((all[sb]) AND (Clinical Prediction Guides/Broad[filter]))) '
            f'AND ((excludepreprints[Filter])) '
            f'AND (("{date_str}"[Date - Create] : "{date_str}"[Date - Create]))')
```

**动态总页数提取**（三种 HTML 来源依次尝试）：

```python
chunk = soup.select_one("div.search-results-chunk")
if chunk:
    pages_amount = int(chunk.get("data-pages-amount", "").replace(",", ""))
    max_page = int(chunk.get("data-max-page", ""))
    return min(pages_amount, max_page), "search-results-chunk"
label = soup.select_one("label.of-total-pages")     # "of 23,680"
m = re.search(r'of\s+([\d,]+)', label.get_text(strip=True))
```

**curl_cffi + 插页重试**（"checking your browser" 等待后重试）：

```python
resp = curl_requests.get(BASE_URL + "/", headers=HEADERS, params=params, cookies=self._cookies)
text_lower = resp.text.lower()
if "checking your browser" in text_lower:
    print(f"checking your browser 等待5秒重定向 retry {attempt + 1}")
    time.sleep(5)
    continue
```

**DOI 三重备选**（详情页）:

```python
doi_tag = soup.find("span", class_="identifier doi")
if doi_tag and (doi_link := doi_tag.find("a", class_="id-link")):
    doi = doi_link.get_text(strip=True)
if not doi:
    meta_doi = soup.find("meta", {"name": "citation_doi"})
    doi = meta_doi.get("content", "") if meta_doi else ""
if not doi:
    doi_citation = soup.find("span", class_="citation-doi")
    doi = doi_citation.get_text(strip=True).replace("doi:", "").strip().rstrip(".")
```

**列表+详情合并**（详情优先，保留列表独有字段）：

```python
def merge_article(list_data, detail_data):
    merged["title"] = detail_data.get("title") or list_data.get("title_list", "")
    merged["authors"] = detail_data.get("authors") or [
        {"name": n, "affiliation_ids": [], "affiliation_titles": []}
        for n in list_data.get("authors_list", [])]
    merged["snippet"] = list_data.get("snippet", "")          # 列表页独有
    merged["journal_citation"] = list_data.get("journal_citation", "")
    return merged
```

**PDF 下载 + 魔数校验 + `_reference.pdf` 回退**：

```python
filename = f"{hashlib.md5(key.encode('utf-8')).hexdigest()}.pdf"
if doi.startswith('10.1186') or doi.startswith('10.1038'):
    pdf_url = f"https://link.springer.com/content/pdf/{doi}_reference.pdf"
if response.status_code == 200 and response.content.startswith(b'%PDF'):
    with open(filepath, 'wb') as f:
        f.write(response.content)
```

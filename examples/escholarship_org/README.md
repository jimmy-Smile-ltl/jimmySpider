# 加州大学学术门户抓取 (escholarship_org)

## 站点

- 站点页面：https://escholarship.org — 加州大学（University of California）研究门户，聚合 UC 各校区、各院系的学位论文、期刊文章与研究报告
- 搜索 API：`/api/pageData/search`（GET JSON，请求头带 `X-Requested-With: XMLHttpRequest`），强制每页 10 条，单次查询结果上限 100,000 条
- 详情页：SPA（Next.js 风格），页面数据以 `window.jscholApp_initialPageData = {...}` 内嵌在 `<script>` 标签中
- 站点部署在 **AWS WAF** 之后，生产环境需从真实浏览器获取会话 cookie（`aws-waf-token`）

## 展示特性

- **JSON 搜索 API 直抓**：不解析 HTML 列表，直接请求 XHR 接口拿 `searchResults` 数组，单条记录一次带齐标题/摘要/作者/导师/版权等字段
- **年份分片突破截断**：单查询上限 100,000 条，而近两年的学位论文就超此量 — 按年份切分搜索范围：2000 年前 10 年一批、2000-2009 两年一批、2010 起每年一批（1960→2026 共 41 个范围），任一查询都远低于上限
- **范围内多线程分页**：每年份范围第 0 页先探 `count`，算出 `ceil(count/10)` 总页数后，其余页交给 `ThreadPoolExecutor`（5 线程）并发抓取
- **Redis 游标断点**：进度 = 下一个年份范围索引；失败页记 `{year_start, year_end, page}` 入错误集，主流程前后最多 3 轮重试
- **内嵌 JSON 提取**（详情页亮点）：正则从 `<script>` 提取 `window.jscholApp_initialPageData` 整个 JSON 对象，再按业务结构映射字段 — 应对 SPA 站点的通用模式
- **共享并发更新引擎**：`HandleMongoDB.update_batch_in_bulk_loop`（批量 + ThreadPoolExecutor + Redis `last_id` 断点 + 连续失败代理刷新），单文档内另带 5 次重试
- **AWS WAF 会话管理**：cookie 刻意不硬编码，运行时从真实浏览器获取后注入 `self.cookies`（代码留空）
- **`_id` 去重**：`generate_string_id(eschol_id)`，重复抓取 upsert 不产生重复文档
- **原始 HTML 落盘**：详情 HTML 按 `_id` 保存并记录 `html_path`，已抓过的自动跳过

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_list.py` | 列表爬虫：年份分片搜索 API + 5 线程并发分页，写入 MongoDB（`_id` = eschol_id MD5） |
| `spider_detail.py` | 详情爬虫：读库中 `detail_url`，提取页内 `jscholApp_initialPageData` JSON，解析字段、存 HTML 并回写 |

## 运行方式

```bash
cd examples/escholarship_org
python spider_list.py     # 第一步：按年份分片抓搜索 API
python spider_detail.py   # 第二步：读库抓详情，提取内嵌 JSON 补全
```

## 前置条件

- **AWS WAF cookie**：详情爬虫位于 AWS WAF 之后，运行前需从真实浏览器打开 https://escholarship.org 获取会话 cookie（含 `aws-waf-token`），注入 `spider_detail.py` 中的 `self.cookies`（代码中已留空，禁止硬编码）
- 依赖服务：
  - **Redis**：年份范围游标（`{表名}_progress`）、失败页（`{表名}_error_pages`）、详情断点（`{表名}_detail_last_id`）
  - **MongoDB**：结果存储与详情回写，collection 名 = 目录名 `escholarship_org`
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
- 代理为可选项（`flush_state` 依赖 handler 装配 `proxyUtil`）

## 爬虫架构

**spider_list.py**：

```
run_all()
 ├─ build_year_ranges()  # 1960-1999 按10年 / 2000-2009 按2年 / 2010-2026 按1年
 ├─ 先 _retry_errors()（最多 3 轮）
 ├─ 读取断点 year_range 索引
 ├─ for yi → year_ranges[]
 │   ├─ 第 0 页：读 count → total_pages = ceil(count / 10)
 │   ├─ 第 0 页数据入库
 │   └─ ThreadPoolExecutor(5) 并发 1..N-1 页
 │       ├─ 成功 → save_result()
 │       └─ 失败 → error_set({year_start, year_end, page})
 │   └─ 每范围完成 → 游标 +1
 ├─ 全部完成且错误集清空 → 清除游标
```

**spider_detail.py**：

```
run_all()
 └─ db_manager.update_batch_in_bulk_loop(
       filter={detail_url 存在, html_path 不存在},
       update_func=handle_one_doc,     # 抓详情(失败重试5次) → 提取内嵌JSON → 存HTML → 返回更新字段
       sort_field="_id", resume_from_id=last_id_cache,
       batch_size=100, max_workers=5, page_size=1000)
```

数据流向：搜索 API JSON → 条目（标题/摘要/作者/导师/年份/类型）→ MongoDB；详情页 HTML → `<script>` 内嵌 JSON → 字段映射（attrs 子对象、citation、相关文章等）→ 原文档回写 + 原始 HTML 落盘。

## 核心代码片段

**年份分片规则**（突破搜索结果 10 万条上限的核心策略）：

```python
@staticmethod
def build_year_ranges() -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    for start in range(1960, 2000, 10):   # 2000年以前 10年一批
        ranges.append((start, start + 9))
    for start in range(2000, 2010, 2):    # 2000-2009 2年一批
        ranges.append((start, start + 1))
    for start in range(2010, 2027):       # 2010-2026 1年一批
        ranges.append((start, start))
    return ranges
```

**第 0 页探总数 + 并发翻页**：

```python
data_p0 = self._fetch_api(ys, ye, 0)
total_count = int(data_p0.get("count", 0))
total_pages = (total_count + 9) // 10
with ThreadPoolExecutor(max_workers=5) as executor:
    future_to_page = {executor.submit(self._fetch_one_page, ys, ye, p): p
                      for p in range(1, total_pages)}
    for future in as_completed(future_to_page):
        rows = self._parse_results(data, ys, ye, page_num)
        if rows:
            self.save_result(insert_list=rows)
```

**详情页内嵌 JSON 提取**（SPA 站点数据获取的通用模式）：

```python
for s in soup.find_all("script"):
    if s.string and "jscholApp_initialPageData" in s.string:
        m = re.search(r"window\.jscholApp_initialPageData\s*=\s*(\{.*\})\s*;?\s*$",
                      s.string, re.DOTALL)
        if m:
            page_data = json.loads(m.group(1))
            return cls.parse_detail_json(page_data, source_url)
```

**attrs 子对象平铺**（抽象摘要去 HTML 化）：

```python
result["abstract"] = attrs.get("abstract", "")
if result["abstract"]:
    abs_soup = BeautifulSoup(result["abstract"], "html.parser")
    result["abstract_text"] = abs_soup.get_text(separator="\n", strip=True)
```

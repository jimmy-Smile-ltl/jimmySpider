# 查理大学 DSpace 仓储抓取 (cuni_cz)

## 站点

- 站点页面：https://dspace.cuni.cz — 查理大学（Charles University）DSpace 学术仓储，收录论文、学位论文、学术出版物等，按「学院 (College) → 文献类型 (Type)」组织，每个类型是一个可分页的浏览列表 URL
- 列表页：经典 HTML 分页（`ul.pagination`，`?page=N` 翻页）
- 详情页：`/handle/...`，追加 `?show=full` 可拿到完整视图（`ds-includeSet-table` 表格 + 完整文件列表）
- 种子数据：`college_list_with_types.json`（学院及其各文献类型 URL，人工整理，不随示例发布）

## 展示特性

- **经典 list + detail 双爬虫模式**：先跑 `spider_list.py` 把列表条目（含 `detail_url`）写入 MongoDB，再跑 `spider_detail.py` 读库抓详情补全字段
- **单类型内多线程分页**：每个「学院/类型」第 1 页先行抓取以发现总页数，之后 2..N 页交给 `ThreadPoolExecutor`（5 线程）并发抓取 — 用 `SingleRequestHandler` 同步请求 + 线程池实现的高吞吐翻页
- **双层断点续爬**：`{college_idx, type_idx}` 游标 + 每类型独立的「已完成页」Set（`{表名}_pages_done_{ci}_{ti}`），断点恢复时已完成的页直接跳过
- **多轮错误重试**：失败页（含学院/类型/页码上下文）入 `error_set`，主流程前后最多 3 轮重试
- **详情页结构化解析**：DC meta 标签（`DC.title` / `DC.creator` / `citation_*`）、`.item-page-field-wrapper` 字段、`ds-includeSet-table` 完整视图、文件列表（文件名/大小/URL）三套解析器
- **共享并发更新引擎**：`HandleMongoDB.update_batch_in_bulk_loop` — 批量拉取 + ThreadPoolExecutor + Redis `last_id` 断点 + 连续失败触发代理刷新（`flush_state`）
- **原始 HTML 落盘**：详情页 HTML 按 `_id` 保存，`html_path` 写入文档，再次运行自动跳过已抓详情（`html_path: {$exists: False}` 过滤）
- **`_id` 防重**：`generate_string_id(detail_url)`，列表/详情均按 `_id` upsert

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_list.py` | 列表爬虫：学院/类型双重循环 + 5 线程并发分页，写入 MongoDB（`_id` = detail_url MD5） |
| `spider_detail.py` | 详情爬虫：读库中 `detail_url`，`?show=full` 抓取、解析 meta/字段/文件，保存 HTML 并回写 |
| `college_list_with_types.json` | 学院 + 文献类型 URL 种子数据（spider_list.py 的运行时依赖，示例未随附） |

## 运行方式

```bash
cd examples/cuni_cz
python spider_list.py     # 第一步：抓列表，种子 MongoDB
python spider_detail.py   # 第二步：读库抓详情，补全字段
```

## 前置条件

- **`college_list_with_types.json` 需自行准备**：格式为 `[{index, name_cz, name_en, college_url, type_list: [{name_cz, name_en, type_url, count}]}]`，放本目录（`college_list_path` 指向同目录）
- 依赖服务：
  - **Redis**：进度游标（`{表名}_progress`）、错误页（`{表名}_error_pages`）、每类型已完成页（`{表名}_pages_done_*`）、详情断点（`{表名}_detail_last_id`）
  - **MongoDB**：列表结果与详情回写，collection 名 = 目录名 `cuni_cz`
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
- 无需登录；代理为可选项（`flush_state` 依赖 handler 装配了 `proxyUtil` 才生效）

## 爬虫架构

**spider_list.py**：

```
run_all()
 ├─ 加载 college_list_with_types.json，先 _retry_errors()（最多 3 轮）
 ├─ 读取断点 {college_idx, type_idx}
 ├─ for ci → college_list[]
 │   └─ for ti → type_list[]
 │       └─ _process_type(ci, ti)
 │           ├─ 第 1 页先行：发现 total_pages，解析入库，标记 done
 │           ├─ ThreadPoolExecutor(5) 并发抓 2..N 页
 │           │   ├─ 成功 → save_result + 标记页 done
 │           │   └─ 失败 → error_set（带 ci/ti/page 上下文）
 │           └─ 类型完成且无残留错误 → 清除该类型页级标记
 ├─ 全部完成且无错误 → 清除游标；有残留 → 保留以便重试
```

**spider_detail.py**：

```
run_all()
 └─ db_manager.update_batch_in_bulk_loop(
       filter={detail_url 存在, html_path 不存在},
       update_func=handle_one_doc,       # 抓详情 → 解析 → 存 HTML → 返回更新字段
       sort_field="_id", resume_from_id=last_id_cache,
       batch_size=100, max_workers=10, page_size=1000,
       flush_state=flush_state)          # 连续失败 → 刷新代理
```

数据流向：列表页 HTML → 条目（标题/作者/出版年/答辩状态/答辩日期）→ MongoDB；详情页 HTML → meta 标签 + 字段表 + 文件列表 → 原文档回写 + 原始 HTML 落盘。

## 核心代码片段

**总页数解析**（`li.last-page-link` 优先，数字集合兜底）：

```python
last_tag = pagination.select_one("li.last-page-link a")
if last_tag:
    last_text = last_tag.get_text(strip=True)
    if last_text.isdigit():
        return int(last_text), current_page
nums = [int(t.get_text(strip=True)) for t in pagination.select("li")
        if t.get_text(strip=True).isdigit()]
return max(nums) if nums else current_page, current_page
```

**单类型并发分页 + 已完成页跳过**（断点恢复核心）：

```python
done_pages = self._load_done_pages(college_idx, type_idx)
pending = [p for p in range(2, total_pages + 1) if p not in done_pages]
with ThreadPoolExecutor(max_workers=5) as executor:
    future_to_page = {executor.submit(self._fetch_one_page, type_url, p): p
                      for p in pending}
    for future in as_completed(future_to_page):
        page_num, html_text = future.result()
        data = self.parse_list_page(html_text, college, type_item, page_num)
        if data:
            self.save_result(insert_list=data)
            self._mark_page_done(college_idx, type_idx, page_num)  # 页级断点
```

**详情页 meta 标签解析**（同名标签去重为单值或列表）：

```python
def parse_meta_tags(soup) -> Dict:
    raw = defaultdict(list)
    for tag in soup.find_all("meta", attrs={"name": True, "content": True}):
        content = tag["content"].strip()
        if content:
            raw[tag["name"].strip()].append(content)
    return {name: (vals[0] if len(vals) == 1 else vals)
            for name, vals in raw.items()}
```

**文件列表双解析器**（完整视图优先，简版兜底）：

```python
files = cls.parse_files_full(soup)      # div.file-list > div.file-wrapper 的 dl/dt/dd
if not files:
    files = cls.parse_files_simple(soup) # h5.item-list-entry a，正则拆 "文件名 (大小)"
```

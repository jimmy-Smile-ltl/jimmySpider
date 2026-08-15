# 中央结算公司《债券》期刊抓取 (ccdc_bond_journal)

本地 JSON 期次清单驱动的期刊文章抓取：`issues.json` 保存「年份 → 期次 URL」，主流程按期次顺序解析，断点精确到期次。

## 站点

- 站点页面：https://www.ccdc.com.cn/Fmi/Thinktank/Article/ — 中央国债登记结算有限责任公司（CCDC）《债券》期刊栏目，收录中国债券市场研究文章
- 数据来源：各期次目录页 HTML（如 `https://www.ccdc.com.cn/Fmi/Thinktank/Article/cb2026/cb202604/`），按期次返回当期文章列表（无分页）
- 期次清单由本地 `issues.json` 维护（年份 → 期次标题与 URL），无需爬期刊列表页

## 展示特性

- **本地 JSON 驱动采集**：`load_issues()` + `flatten_issues()` 把 `issues.json` 展平为 `[{year, issue_title, issue_url}]` 线性列表，扩展清单即可覆盖更多期次
- **期刊页结构解析**：`div.journal_qlist` 分类块（`div.journal_stitle` 为分类名），块内每个 `li` 提取 标题（`a.journal_info`）/ 作者（`div.writerList span`，剥掉「作者:」前缀）/ `docPubUrl` / `docId` / `curPage`
- **`_id` 三级降级**：`generate_string_id(doc_id or doc_url or title)` — 文档 ID → 文档 URL → 标题，依次兜底生成唯一 ID
- **Redis 断点续爬**（key 前缀 `ccdc_bond_journal_`）：
  - `log_issue` — 记录 `{"issue_idx": N}` 断点，中断后从该期次精确恢复
  - `error_issue_set` — 失败期次（JSON 编码的 `{"issue_idx"}`）入集合，主流程后最多 3 轮重试
- **完成即清断点**：主流程跑完后 `log_issue.clear_value()`，下次运行从头开始
- **限速**：期次间 `time.sleep(1)`；AJAX 风格请求头（`X-Requested-With: XMLHttpRequest`）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：读取期次清单 → 逐期解析 → 入库 → 断点与重试 |
| `issues.json` | 期次清单（`[{year, year_url, issues: [{issue_title, issue_url}]}]`），运行前可按需补充新期次 |

## 运行方式

```bash
cd examples/ccdc_bond_journal
python spider.py
```

## 前置条件

- 无需登录 cookie；期次页面直接 GET 即可
- 依赖服务：
  - **Redis**：断点续爬（key `ccdc_bond_journal_log_issue` / `ccdc_bond_journal_error_issue_set`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `ccdc_bond_journal`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ load_issues() + flatten_issues()    # issues.json → [{year, issue_title, issue_url}]
 ├─ 读取 log_issue 断点 → start_idx
 ├─ for issue_idx in [start_idx, len(issue_list)):
 │   ├─ get_issue_page(issue_url)       # SingleRequestHandler GET 期次页
 │   ├─ parse_issue_page()              # journal_qlist 分类块 × li 文章条目
 │   ├─ save_result(insert_list=...)    # 按 _id upsert 写 MongoDB
 │   ├─ 请求失败 → error_issue_set.add_to_set({"issue_idx"})
 │   ├─ log_issue.record_string({"issue_idx": idx+1})   # 期次级断点
 │   └─ time.sleep(1)                   # 限速
 ├─ log_issue.clear_value()             # 主流程完成，清断点
 └─ 最多 3 轮 handle_error_issue()      # 重试 error_issue_set 中失败期次
```

数据流向：期次页 HTML → 分类块 × 文章条目（年份/期刊/分类/标题/作者/文档ID/页面路径）→ MongoDB `ccdc_bond_journal`。

## 核心代码片段

**期次清单展平**（JSON 树 → 线性列表）：

```python
@staticmethod
def flatten_issues(issue_tree: List[Dict]) -> List[Dict]:
    results = []
    for year_item in issue_tree:
        year = year_item.get("year")
        for issue in year_item.get("issues", []):
            results.append({
                "year": year,
                "issue_title": issue.get("issue_title", ""),
                "issue_url": issue.get("issue_url", ""),
            })
    return results
```

**单条文章解析**（`div[name=...]` 数据位提取 + `_id` 三级降级）：

```python
unique_key = doc_id or doc_url or title      # 三级降级
results.append({
    "_id": generate_string_id(unique_key),
    "年份": issue_info.get("year", ""),
    "期刊": issue_info.get("issue_title", ""),
    "期刊URL": issue_info.get("issue_url", ""),
    "分类": category,                         # div.journal_stitle
    "标题": title,                            # a.journal_info
    "作者": author,                           # 已剥掉「作者:」前缀
    "url": doc_url,                           # div[name=docPubUrl]
    "文档ID": doc_id,                         # div[name=docId]
    "页面路径": cur_page,                     # div[name=curPage]
})
```

**失败期次重试**（错误集合消费，仍有失败则等下一轮）：

```python
for error_key in error_keys:
    issue_info = self._decode_cache(error_key)
    issue_idx = issue_info.get("issue_idx")
    if issue_idx is None or issue_idx >= len(issue_list):
        self.error_issue_set.remove_from_set(error_key)
        continue
    issue = issue_list[issue_idx]
    html_text = self.get_issue_page(issue.get("issue_url", ""))
    if html_text:
        data_list = self.parse_issue_page(html_text, issue)
        if data_list:
            self.save_result(insert_list=data_list)
        self.error_issue_set.remove_from_set(error_key)
    else:
        return False                          # 仍有失败，等待下一轮重试
```

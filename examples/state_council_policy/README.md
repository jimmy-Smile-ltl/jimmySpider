# 国务院政策文件库抓取 (state_council_policy)

## 站点

- 站点页面：https://sousuo.www.gov.cn/zcwjk/policyDocumentLibrary — 国务院政策文件库，收录国务院及各部门政策文件、解读等
- 列表接口：https://sousuo.www.gov.cn/search-gov/data（GET，参数 `t=zhengcelibrary`），返回 JSON，按类目（`searchVO.catMap`）聚合条目，每条含详情页 `url`
- 详情页：`www.gov.cn` 下的 HTML 页面，元信息在表格 `table.bd1` 等键值对结构中，正文在 `div.article` / `table.marauto.table2` 等区块

## 展示特性

- **列表页 + 详情页两段式抓取**：列表接口只提取详情 URL，详情页单独请求解析正文后入库，数据完整度更高
- **详情页多线程并发**：`ThreadPoolExecutor(max_workers=5)` + `as_completed`，每页最多 5 个详情并发抓取
- **BeautifulSoup 详情解析**：
  - 表头键值对（`table.bd1` 或 `pctoubukuang1 table`）逐 `<tr>` 配对提取「键: 值」
  - 正文用多候选 selector 依次尝试（`div.article` → `table.marauto.table2` → `table.pages_content` → `div.pages_content`），配合框架 `extract_content_recursively` 递归提取
  - 文档类型从面包屑 `BreadcrumbNav` 倒序取第一个不含数字的段，并剔除 `pages_print.mhide` 干扰节点
- **HTML 原文存档**：每个详情页正文通过 `html_saver.save_html` 落盘，`html_path` 一并入库
- **三级断点续爬**：列表页码（`log_page_num`）、失败列表页（`error_page_set`）、失败详情页（`error_detail_set`，携带完整上下文）分别记录，中断后自动恢复
- **统一重试机制**：主流程结束后，失败列表页逐页重试，失败详情页按每批 20 条重试，最多 3 轮
- **健壮性工具**：`safe_extract_json` 带默认值安全取嵌套字段；`generate_string_id(url)` 生成详情页 `_id`（按 URL 去重）
- **无需自行装配请求器**：详情请求使用基类自动装配的 `self.single_fetcher`（默认 SingleRequestHandler）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：列表分页 → 详情并发 → 入库 → 断点与重试 |

## 运行方式

```bash
cd examples/state_council_policy
python spider.py
```

## 前置条件

- 无需登录 cookie（代码中 cookie 字典留空即可）
- 请求使用 `verify=False`（站点证书链在部分环境校验失败，自行评估风险）
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `state_council_policy_`，如 `state_council_policy_page_num`、`..._error_detail_set`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `state_council_policy`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
- 依赖库：`beautifulsoup4`（框架依赖已含）

## 爬虫架构

```
run_list()
 ├─ 从 log_page_num 恢复页码
 ├─ while current_page <= max_page
 │   ├─ get_one_page(page)               # GET search-gov/data，n=5 条/页
 │   ├─ extract_one_page(response)       # catMap 多类目汇总，计算 max_page / has_next
 │   ├─ handle_detail_batch(data_list)   # 5 线程并发抓详情
 │   │   ├─ handle_one_detail(url)
 │   │   │   ├─ 请求详情页
 │   │   │   ├─ html_saver.save_html()   # 原文存档
 │   │   │   ├─ extract_one_detail()     # 表头键值对 + 正文 + 类型
 │   │   │   └─ _id = generate_string_id(url)
 │   │   └─ 失败详情入 error_detail_set
 │   ├─ save_result(insert_list)         # MongoDB upsert
 │   └─ 失败列表页入 error_page_set
 ├─ 主流程结束，最多 3 轮重试
 │   ├─ handle_error_page()              # 失败列表页重试
 │   └─ handle_error_detail()            # 失败详情按 20 条/批重试
 └─ 全部清空即完成
```

数据流向：列表 JSON（含详情 URL）→ 并发抓详情 HTML → 结构化解析（键值对元信息 + 递归正文 + 面包屑类型）→ 与列表数据合并 → MongoDB（`_id` 为详情 URL 的 MD5）。

## 核心代码片段

**详情页多线程并发**（future 映射回原 dict，方便错误定位与上下文重试）：

```python
with ThreadPoolExecutor(max_workers=5) as executor:
    future_to_detail = {
        executor.submit(self.handle_one_detail, detail_dict): detail_dict
        for detail_dict in detail_dict_list
    }
    for future in as_completed(future_to_detail):
        detail_dict = future_to_detail[future]
        try:
            result = future.result()
            if result:
                results.append(result)
                if handle_error:
                    self.error_detail_set.remove_from_set(detail_dict)
        except Exception as e:
            self.error_detail_set.add_to_set(detail_dict)
```

**表头键值对解析**（`td` 两两配对，奇数个说明字段缺失并打印告警）：

```python
for tr in head_table.select("tr"):
    tds = tr.select("td")
    if len(tds) % 2 != 0:
        print(tds)
    else:
        for idx in range(0, len(tds), 2):
            key = self.clean_text(tds[idx].get_text(strip=True))
            value = tds[idx + 1].get_text(strip=True)
            one_row[key] = value
```

**翻页终止判断**：各分类 `listVO` 长度均小于 5（每页上限）即视为无下一页，`has_next = not all(cat_list_len)`。

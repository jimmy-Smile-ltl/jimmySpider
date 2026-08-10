# 中国银行金融市场宏观研究抓取 (boc_fimarkets)

## 站点

- 站点页面：https://www.boc.cn/fimarkets/summarize/ — 中国银行金融市场「宏观经济研究」栏目，发布宏观经济分析报告与公告，正文内嵌 PDF/Word 附件
- 列表页：`fimarkets/summarize/index.html` → `index_1.html` → `index_2.html` …（GET，HTML）
- 详情页：每条新闻独立详情页（HTML），正文在 `div.sub_con`，附件为该节点内的 `<a href>` 链接

## 展示特性

- **list+detail 两阶段模式**：列表页解析标题/日期/链接，逐条抓详情页提取正文与附件
- **分页 URL 规律拼接**：`page<=1` 用 `index.html`，否则 `index_{page-1}.html`——演示非标准分页规则的推导与实现
- **编码自适应**：`response.encoding = response.apparent_encoding` 处理 GBK/UTF-8 混合编码页面（银行老站点的常见坑）
- **日期标准化**：列表页 `[2024-05-16]` 文本经 `convert_date_robust`（框架 `jimmyspider.datetime_utils`）智能解析为标准日期
- **正文递归清洗**：`extractSoup.extract_content_recursively` 把 `div.sub_con` 树递归提取为纯文本，去除样式与空节点
- **附件拆分入库**（本示例亮点）：一个详情页的每个附件单独成一条记录——`_id = MD5(detail_url::file_url)`，正文在每条附件记录中冗余保存；无附件时正文单条入库
- **空页终止策略**：连续 1 页无数据即视为列表结束（`empty_pages >= 1`），避免对不存在的长分页无限请求
- **三级错误重试**：列表失败页（`error_page_set`）与详情失败页（`error_detail_set`）各自独立 Set，主流程后各重试 3 轮

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：列表分页 + 详情抓取 + 附件拆分 + 双级重试 |

## 运行方式

```bash
cd examples/boc_fimarkets
python spider.py
```

## 前置条件

- 无需登录：`self.cookies` 留空即可（原站点级 Cookie 非登录凭证）
- 依赖服务：
  - **Redis**：断点续爬（`boc_fimarkets_log_page` 存 JSON 断点 / `boc_fimarkets_error_page_set` / `boc_fimarkets_error_detail_set`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `boc_fimarkets`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ 读取序列化断点 {page}
 ├─ while True
 │   ├─ build_list_url(page)           # index.html / index_{page-1}.html
 │   ├─ parse_list_page(html)          # 标题/日期/详情链接
 │   └─ handle_detail_list(detail_list)
 │       ├─ get_detail_page(url)       # 逐条抓详情
 │       ├─ save_detail_records()      # 正文 + 附件拆分入库
 │       └─ 失败详情入 error_detail_set
 │   └─ empty_pages >= 1 时 break（列表结束）
 ├─ 主流程结束，log_page 清空
 ├─ 最多 3 轮 handle_error_page()  重试失败列表页
 └─ 最多 3 轮 handle_error_detail() 重试失败详情页
```

数据流向：列表 HTML → 标题/日期/链接 → 详情 HTML → `div.sub_con` 正文递归清洗 + 附件链接收集 → 每条附件单独入库（`_id = MD5(detail_url::file_url)`）。

## 核心代码片段

**分页 URL 规律拼接**（page<=1 特判 + 序号减一）：

```python
def build_list_url(self, page: int) -> str:
    if page <= 1:
        return urljoin(self.index_base, "index.html")
    return urljoin(self.index_base, f"index_{page - 1}.html")
```

**附件拆分入库**（一个详情页 → N 条记录，正文冗余 + 附件独立 `_id`）：

```python
unique_key = f"{detail_url}::{file_url}" if file_url else detail_url
data_list.append({
    "_id": generate_string_id(unique_key),
    "标题": base_info.get("标题", ""),
    "发布时间": base_info.get("发布时间", ""),
    "url": detail_url,
    "正文内容": content_text,
    "file_url": file_url,
    "file_title": file_title,
    "file_type": file_type,
    "create_time": now_ts,
})
```

**空页终止**（连续无数据即结束，防止对不存在页码的无效请求）：

```python
if detail_list:
    self.handle_detail_list(detail_list)
    empty_pages = 0
else:
    self.log_print.warning(f"  page:{page} 解析无数据")
    empty_pages += 1
...
if empty_pages >= 1:
    self.log_print.print("  已无更多数据")
    break
```

**双级错误重试**（列表页与详情页独立重试，各 3 轮）：

```python
for retry in range(3):
    if self.handle_error_page():
        break
for retry in range(3):
    if self.handle_error_detail():
        break
```

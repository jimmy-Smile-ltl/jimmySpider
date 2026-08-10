# 高盛研报检索抓取 (gspublishing)

## 站点

- 站点页面：https://www.gspublishing.com/research — 高盛（Goldman Sachs）全球研报检索平台
- 数据接口：`gspublishing.com/research/search/reports/advanced-search`（POST JSON），请求体为复杂查询语法（`facets` / `language` / `sort` / `limitTo` / `filter`），响应 JSON 的 `documents` 数组为研报记录、`pagination.pageCount` 为总页数

## 展示特性

- **POST JSON 复杂查询 payload**：`json.dumps(payload, separators=(",", ":"))` 压缩序列化后作为 `data` 提交，`Content-Type: application/json`——演示投行类站点的检索式接口
- **毫秒时间戳格式化**：`publicationDateTime` / `lastPublishedDateTime` / `lastModifiedDateTime` 三个字段统一转 `YYYY-MM-DD HH:MM:SS`
- **附件链接构建**：`path` / `downloadPath` 相对路径用 `urljoin` 拼绝对 URL，按扩展名自动提取 `file_type`
- **raw_data 全量保留**：除映射出的 12 个字段外，原始记录整体入库（`raw_data`），后续需要新字段时无需重新抓取
- **JSON 序列化断点**：`{"page": N}` 写入 Redis，中断后从页码恢复；每页间 `time.sleep(1)` 限速
- **错误页重试**：失败页入 Set，主流程后最多 3 轮重试
- **`_id` 降级策略**：优先取研报 ID，缺失时回退为 URL MD5

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：POST JSON 分页抓取、字段映射、断点与重试 |

## 运行方式

```bash
cd examples/gspublishing
python spider.py
```

## 前置条件

- 无需登录：`self.cookies` 留空即可
- 依赖服务：
  - **Redis**：断点续爬（`gspublishing_log_page` 存 JSON 断点 / `gspublishing_error_page_set`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `gspublishing`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ 读取序列化断点 {page}
 ├─ while True
 │   ├─ get_list_page(page)             # POST JSON payload（压缩序列化）
 │   ├─ parse_records(documents)        # 时间戳格式化 + urljoin 拼链接
 │   ├─ save_result()                   # MongoDB upsert（_id = 研报 ID）
 │   ├─ record_string({page+1})         # 每页后落盘断点
 │   └─ page >= pagination.pageCount 时 break，失败则记录错误页后退出
 ├─ 主流程结束，log_page 清空
 └─ 最多 3 轮 handle_error_page() 重试失败页
```

数据流向：POST JSON 检索 → `documents` 记录映射（毫秒时间戳 → 日期、相对路径 → 绝对 URL、扩展名 → file_type）→ MongoDB（`_id = 研报 ID`，raw_data 全量保留）。

## 核心代码片段

**POST JSON 复杂查询 payload**（压缩序列化 + 检索语法）：

```python
payload = {
    "facets": "()",
    "language": "[\"en\",\"ja\",\"zh\"]",
    "page": page,
    "sort": "time",
    "limitTo": "[\"\"]",
    "filter": "( totalPages IN [1,400])",
    "applyHighlighting": True,
}
response = self.single_fetcher.fetch(
    self.list_api_url,
    headers=self.headers,
    cookies=self.cookies,
    data=json.dumps(payload, separators=(",", ":")),
    method="POST",
    check_size=False,
)
```

**毫秒时间戳格式化**（缺失/异常均安全返回空串）：

```python
@staticmethod
def _format_ts(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""
```

**链接构建 + 原始记录保留**（urljoin 拼绝对 URL，raw_data 全量入库）：

```python
url = urljoin(self.base_url, record.get("path", "")) if record.get("path") else ""
...
results.append({
    "_id": generate_string_id(record.get("id") or url),
    "标题": record.get("title", ""),
    ...
    "raw_data": record,
})
```

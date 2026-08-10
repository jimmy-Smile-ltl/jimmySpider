# 中国货币网信用评级公告抓取 (chinamoney)

## 站点

- 站点页面：https://www.chinamoney.com.cn/chinese/zxpjbgh/ — 中国货币网「信用评级报告」栏目
- 数据接口：`https://www.chinamoney.com.cn/ags/ms/cm-u-notice-issue/ratingAnNotice`（POST form 表单），按「年份 × 页码」返回 JSON（每页 30 条）
- 覆盖 2004 年至当前年份的信用评级公告（评级报告标题、发表机构、发布时间、PDF 链接）

## 展示特性

- **POST API 分页抓取**：表单参数含固定值（`channelId=2564` / `drftClAngl=11` / `scnd=1104`），`pageNo` / `pageSize` 控制分页，`startDate` / `endDate` 按年份切分时间范围，`pageTotalSize` 为总页数
- **字段标准化**：`parse_records()` 将接口原始字段（`title` / `prefix` / `releaseDate` / `draftPath` / `suffix` / `contentId`）映射为统一中文字段（标题 / 发表机构 / 发布时间 / url / file_type）
- **PDF 下载链接构造**：`file_url` 由 `fileDownLoad.do?mode=open&contentId=...` 拼接，配合 `file_type` 供后续 `file_saver` 批量下载
- **Redis 断点续爬**（key 前缀 `chinamoney_`）：
  - `log_year_page` — 记录 `{"year", "page"}` 断点，中断后从断点精确恢复
  - `error_page_set` — 失败页入集合，主流程后最多 3 轮重试
- **限速**：页间 `time.sleep(1)`，降低触发风控的概率

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：POST 分页 → 字段标准化 → 入库 → 断点与重试 |

## 运行方式

```bash
cd examples/chinamoney
python spider.py
```

## 前置条件

- 无需登录 cookie；`test_url` 用于请求处理器装配代理（未配置代理则直连）
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `chinamoney_`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `chinamoney`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ 读取 log_year_page 断点 {"year", "page"}，从断点恢复
 ├─ for year in start_year..end_year
 │   └─ handle_one_year(year, start_page)
 │       └─ while True
 │           ├─ get_list_page(year, page)   # POST 表单，pageTotalSize 为总页数
 │           ├─ parse_records(records)      # 字段标准化 + _id 生成
 │           ├─ save_result()               # MongoDB upsert
 │           ├─ log_year_page 记录 {"year", "page+1"}
 │           ├─ page >= total_page → break
 │           └─ 请求失败 → 入 error_page_set，跳出本页循环
 ├─ 主流程完成，清空断点
 └─ 最多 3 轮 handle_error_page() 重试失败页
```

数据流向：ratingAnNotice JSON → 字段标准化（标题/机构/时间/详情链接/PDF 链接）→ MongoDB（collection = `chinamoney`）。

## 核心代码片段

**分页请求参数**（按年份切分时间范围）：

```python
data = {
    "channelId": "2564",
    "drftClAngl": "11",
    "scnd": "1104",
    "pageNo": str(page),
    "pageSize": "30",
    "startDate": f"{year}-01-01",
    "endDate": f"{year + 1}-01-01",
    ...
}
response = self.single_fetcher.fetch(
    self.list_api_url, headers=self.headers, cookies=self.cookies,
    data=data, method="POST", check_size=False,
)
```

**字段标准化与 PDF 链接构造**：

```python
detail_url = urljoin(self.base_url, draft_path) if draft_path else ""
file_url = (
    f"{self.file_down_url}?mode=open&contentId={content_id}&priority=0"
    if content_id else ""
)
results.append({
    "_id": generate_string_id(detail_url or content_id),
    "标题": record.get("title", ""),
    "发表机构": record.get("prefix", ""),
    "发布时间": record.get("releaseDate", ""),
    "url": detail_url,
    "file_url": file_url,
    "file_type": record.get("suffix", ""),
})
```

**断点恢复**（主流程开始时读取 `log_year_page`）：

```python
progress_str = self.log_year_page.get_string(default="")
if progress_str:
    progress = json.loads(progress_str)
    start_year = progress.get("year", self.start_year)
    start_page = progress.get("page", 1)
```

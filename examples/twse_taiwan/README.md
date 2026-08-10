# 台湾证券交易所公开资讯观测站抓取 (twse_taiwan)

## 站点

- 站点页面：https://mops.twse.com.tw/mops/#/web/t146sb10 — 台湾证券交易所「公开资讯观测站」重大讯息公告查询
- 数据接口：`https://mops.twse.com.tw/mops/api/t146sb10`（POST JSON），按「民国年份 × 市场类别」分片返回多组公告表格
- 日期为民国纪年（如 `115/03/16`），本示例自动转换为公历入库

## 展示特性

- **金融数据抓取**：覆盖台湾证券交易所四个市场（上市 sii / 上櫃 otc / 興櫃 rotc / 公開發行 pub），默认抓取民国 106~115 年（公元 2017~2026），可按年调整 `start_year` / `end_year`
- **动态表格解析**：接口返回 `header + titles + data` 结构，每条公告的列不固定，按 `titles` 与 `data` 逐列对齐组装字典
- **民国 → 公历日期转换**：`roc_to_gregorian()` 处理 1~3 位民国年份（+1911），输出 `YYYY-MM-DD`
- **详情页并发抓取**：`ThreadPoolExecutor(max_workers=5)` 并发抓取详情页，`extract_content` 提取正文、`html_saver` 保存 HTML 快照
- **Redis 断点续爬**（key 前缀 `twse_taiwan_`）：
  - `completed_segment_set` — 已完成分片（年份-市场）跳过
  - `complete_detail_type` — 已处理的公告类型去重
  - `error_year_type_set` / `error_detail_set` — 失败分片 / 失败详情入集合，主流程后最多 5 轮重试

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：列表分片 → 动态表格解析 → 并发详情 → 入库 → 断点与重试 |

## 运行方式

```bash
cd examples/twse_taiwan
python spider.py
```

## 前置条件

- 无需登录 cookie；`test_url` 用于请求处理器装配代理（未配置代理则直连）
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `twse_taiwan_`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `twse_taiwan`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
- 默认抓取 2017~2026 年全量数据（48 个分片），量较大；调试时可临时缩小年份范围

## 爬虫架构

```
run_list()
 ├─ for year in 106..115 × for marketKind in [sii, otc, rotc, pub]
 │   ├─ completed_segment_set 命中 → 跳过
 │   ├─ handle_one_year_type(year, marketKind)
 │   │   ├─ get_one_year_type()       # POST JSON 列表接口（分片粒度：年 × 市场）
 │   │   ├─ extract_one_year_type()   # header + titles + data 动态表格 → data_dict_list
 │   │   └─ 每个 header（公告类型）：
 │   │       ├─ complete_detail_type 命中 → 跳过
 │   │       ├─ handle_detail_batch() # ThreadPoolExecutor(5) 并发抓详情
 │   │       │   ├─ handle_one_detail() → 正文提取 + HTML 快照 + _id
 │   │       │   └─ 失败入 error_detail_set
 │   │       ├─ save_result()         # MongoDB upsert
 │   │       └─ complete_detail_type.add_to_set(header)
 │   └─ completed_segment_set.add_to_set(year-marketKind)
 └─ handle_error()
     ├─ handle_error_page()   # 最多 5 轮重试失败分片
     └─ handle_error_detail() # 最多 5 轮重试失败详情
```

数据流向：MOPS JSON（民国日期）→ 动态表格解析 + 民国→公历 → 并发详情（正文 + HTML 快照）→ MongoDB（collection = `twse_taiwan`）。

## 核心代码片段

**民国日期转公历**（1~3 位年份均可处理）：

```python
def roc_to_gregorian(self, roc_date_str, sep='/', output_format='%Y-%m-%d'):
    parts = roc_date_str.split(sep)
    roc_year_str, month_str, day_str = parts
    gregorian_year = int(roc_year_str) + 1911
    gregorian_dt = datetime.datetime(gregorian_year, int(month_str), int(day_str))
    return gregorian_dt.strftime(output_format)
```

**动态表格按 titles 逐列对齐**（列数不固定，先校验长度再组装）：

```python
for data_item in data:
    if len(data_item) == len(titles):
        temp_dict = {"类型": header}
        for idx in range(len(data_item)):
            if titles[idx].get("main") == "公告日期":
                temp_dict[titles[idx].get("main")] = self.roc_to_gregorian(data_item[idx])
            else:
                temp_dict[titles[idx].get("main")] = data_item[idx]
```

**并发详情抓取**（5 线程 + 失败记录）：

```python
with ThreadPoolExecutor(max_workers=5) as executor:
    future_to_detail = {
        executor.submit(self.handle_one_detail, detail_dict): detail_dict
        for detail_dict in detail_dict_list
    }
    for future in as_completed(future_to_detail):
        result = future.result()
        if result:
            finished_count += 1
            results.append(result)
```

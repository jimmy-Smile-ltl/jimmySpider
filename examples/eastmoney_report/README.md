# 东方财富研报数据抓取 (eastmoney_report)

## 站点

- 站点页面：https://data.eastmoney.com/report/ — 东方财富网研报中心，聚合个股、行业、宏观、策略等券商研报
- 数据接口：`reportapi.eastmoney.com/report/*`，返回 JSON（股票代码、报告标题、机构、评级、发布日期等）
- 研报原文 PDF 托管在 `pdf.dfcfw.com`，可通过 `infoCode` 直接拼出 PDF 直链

## 展示特性

- **SingleRequestHandler 同步请求**：同一处理器覆盖 GET / POST 两种方式，演示 `headers` / `cookies` / `params` / `data` 四种传参姿势
- **多分类分页抓取**：个股研报（POST `list2`）、行业研报（GET `list`）、新股研报（GET `newStockList`）、宏观/策略/券商晨报（GET `jg` + `qType` 3/2/4），每页 50 条，页码由响应的 `TotalPage` 动态推进
- **Redis 断点续爬**：按「分类」记录进度，已完成分类记入 finished 列表直接跳过；页码、当前分类、失败页分别落 Redis，中断后原样恢复
- **失败页自动重试**：请求失败或解析为空时，将 `(category, page)` JSON 序列化写入 Set，主流程结束后统一重试直至清空
- **链接构建规则**：按分类拼接详情页链接，按 `infoCode` 拼接 PDF 直链；`_id` 优先取 `infoCode`，缺失时回退为链接 MD5
- **冒烟验证**：`test_first_pages()` 一键请求六类首页，输出各类总条数与解析条数，便于上线前验证接口可用性

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：六类研报分页抓取、字段清洗、入库、断点与重试 |

## 运行方式

```bash
cd examples/eastmoney_report
python spider.py
```

可选：先跑冒烟验证（`__main__` 中打开 `spider.test_first_pages()` 注释即可），确认六类接口均正常后再 `spider.run_all()`。

## 前置条件

- 无需登录：代码中的追踪类 cookie（`qgqp_b_id`、`st_*` 等）留空即可正常运行，服务器会自动设置
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `{目录名}_`，如 `eastmoney_report_log_page`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `eastmoney_report`，按 `_id` upsert（重复运行自动更新不产生重复文档）
- `jimmyspider` 框架已安装（`pip install -e .`，见仓库根目录 `pyproject.toml`）；Redis / MongoDB 连接参数通过环境变量（`JIMMYSPIDER_REDIS_HOST`、`JIMMYSPIDER_MONGO_URI` 等）或 `jimmyspider.yaml` 配置，默认指向本机
- 如配置了代理（`PROXY_TUNNEL_URL`），SingleRequestHandler 会通过 `test_url` 自动装配代理；无代理配置则直连

## 爬虫架构

```
run_all()
 ├─ 读取断点：log_category / log_page / log_category_finished
 ├─ 遍历 6 个分类 (个股→行业→新股→宏观→策略→券商晨报)
 │   └─ run_category(category, fetch_fn, channel)
 │       ├─ while page <= TotalPage
 │       │   ├─ fetch_fn(page)      # 各类专用 GET/POST 请求
 │       │   ├─ extract_report_list # 字段清洗 + 链接构建 + _id
 │       │   ├─ save_result()       # MongoDB upsert
 │       │   └─ log_page.record_int # 页码落盘
 │       └─ 失败页 (category,page) 入 error_page_set
 ├─ 主流程结束
 └─ handle_error_page() 循环重试失败页，直到 Set 清空
```

数据流向：接口 JSON → 字段映射（`ratingChange` 数字转中文评级变动、`industryName`/`indvInduName` 取一）→ 拼接报告详情链接与 PDF 链接 → 写 MongoDB（`_id = infoCode`）。

## 核心代码片段

**链接构建规则**（详情页与 PDF 直链）：

```python
def build_pdf_link(self, item: Dict) -> str:
    info_code = item.get("infoCode")
    if not info_code:
        return ""
    return f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
```

**分类分页主循环**（总页数随响应动态更新，失败页记入 Set 后统一重试）：

```python
def run_category(self, category_name, fetch_fn, channel_name, start_page):
    total_page = start_page + 100
    page = start_page
    while page <= total_page:
        res_json = fetch_fn(page)
        if res_json:
            total_page = res_json.get("TotalPage", total_page)
            report_list = self.extract_report_list(res_json, channel_name)
            if report_list:
                self.save_result(insert_list=report_list)
            else:
                self.error_page_set.add_to_set(self._encode_cache(
                    {"category": category_name, "page": page}))
            self.log_page.record_int(page)
            page += 1
        else:
            self.error_page_set.add_to_set(self._encode_cache(
                {"category": category_name, "page": page}))
            page += 1
```

**评级变动数字映射**：

```python
mapping = {"0": "调高", "1": "调低", "2": "首次", "3": "维持", "4": "无"}
```

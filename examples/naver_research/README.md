# Naver 研究报告抓取 (naver_research)

## 站点

- 站点页面：https://finance.naver.com/research/ — Naver Finance 研究报告库，聚合韩国券商发布的 6 类研报
- 列表接口：6 个 GET 分页 HTML 页面（`market_info_list` / `invest_list` / `company_list` / `industry_list` / `economy_list` / `debenture_list`），参数为 `page`，响应为韩文 HTML 表格
- 报告类型在 `type_list.json` 中维护（韩文名 `type_kr` + 中文名 `type_cn` + 列表 URL）

## 展示特性

- **国际站点采集**：韩文页面解析，6 类报告共享一套「表格行 → 记录」的解析约定
- **自定义解析模块**（本示例核心亮点）：解析逻辑独立成 `parser.py`，通过 `_PARSER_MAP` 按 `type_url` 路由到 6 个 `parse_xxx` 函数——同类 5 列结构（行情/投资/经济/债券）复用同一实现，6 列结构（个股含股票列、行业含分类列）单独实现
- **表格行类型识别**：跳过表头行（`<th>`）与分割行（`<td colspan=N>`），只保留数据行；附件列提取 PDF 链接、无附件返回 None
- **韩文日期标准化**：`YY.MM.DD → 20YY-MM-DD`、`YYYY.MM.DD → YYYY-MM-DD` 两种格式统一
- **分类维度断点续爬**：`log_category`（当前分类）→ `log_page`（页码）→ `log_category_finished`（已完成分类，直接跳过），三级配合精确恢复
- **总页数提取**：从分页导航 `table.Nnavi td.pgRR a` 的 `?page=N` 参数取末页
- **失败页重试**：`(type_url, page)` 上下文序列化入 Set，主流程后统一重试
- **解析器自测**：`parser.py` 的 `__main__` 用本地 HTML 快照离线验证 6 个解析函数

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫：6 类报告分页抓取、分类断点、入库与重试 |
| `parser.py` | 独立解析模块：路由表 + 6 个解析函数 + 日期/链接工具（无框架依赖） |
| `type_list.json` | 6 类报告的分类配置（韩文名/中文名/列表 URL），运行时依赖 |

## 运行方式

```bash
cd examples/naver_research
python spider.py

# 离线验证解析器（需先自行保存对应 html_*.html 快照到本目录）
python parser.py
```

## 前置条件

- 无需登录：`self.cookies` 留空即可，站点无会话要求
- 依赖服务：
  - **Redis**：断点续爬（`naver_research_log_category` / `naver_research_log_page` / `naver_research_log_category_finished` / `naver_research_error_page_set`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `naver_research`，按 `_id`（详情 URL MD5）upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
- `parser.py` 的 `__main__` 自测依赖本地的 `html_*.html` 快照文件（未随示例发布）

## 爬虫架构

```
run_all()
 ├─ 读取断点：log_category / log_page / log_category_finished
 ├─ 遍历 TYPE_LIST（6 类报告）
 │   ├─ 已完成分类直接跳过
 │   ├─ run_type(type_info, start_page)
 │   │   ├─ get_list_page(type_url, 1)        # 首页（同时取总页数）
 │   │   ├─ get_total_pages(html)             # 从分页导航提取末页
 │   │   └─ for page in [start_page..total]
 │   │       ├─ get_list_page(type_url, page) # GET ?page=N
 │   │       ├─ parse_page(html, type_url)    # 路由到 parse_xxx
 │   │       ├─ decorate_list()               # _id = detail_url MD5
 │   │       ├─ save_result()                 # MongoDB upsert
 │   │       └─ log_page.record_int(page)
 │   └─ log_category_finished.append(type_kr)
 ├─ 主流程结束
 └─ handle_error_page() 循环重试失败页，直到 Set 清空
```

数据流向：GET 列表 HTML → 表格行解析（路由表分发）→ 字段清洗（韩文日期标准化）→ MongoDB（`_id = 详情 URL MD5`）。

## 核心代码片段

**路由表分发**（同一站点多种页面结构的统一入口）：

```python
_PARSER_MAP = {
    "market_info_list": parse_market_info,
    "invest_list":      parse_invest,
    "company_list":     parse_company,
    "industry_list":    parse_industry,
    "economy_list":     parse_economy,
    "debenture_list":   parse_debenture,
}

def get_parser(type_url: str):
    for key, fn in _PARSER_MAP.items():
        if key in type_url:
            return fn
    raise ValueError(f"未知的 type_url，无法匹配解析器: {type_url}")
```

**韩文日期标准化**（YY.MM.DD / YYYY.MM.DD 统一为 ISO 格式）：

```python
def normalize_date(raw: str) -> str:
    s = raw.strip()
    m = re.match(r"^(20\d{2})[.\-](\d{2})[.\-](\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{2})[.\-](\d{2})[.\-](\d{2})$", s)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s
```

**分类断点推进**（已完成分类入 finished 列表，下次启动直接跳过）：

```python
for type_info in TYPE_LIST:
    type_kr = type_info.get("type_kr")
    if type_kr in categories_finished:
        self.log_print.print(f"{type_kr}已完成，跳过...")
        continue
    ...
    self.log_category_finished.append_to_list(type_kr)
```

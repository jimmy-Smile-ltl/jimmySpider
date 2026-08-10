# 梅斯医学指南抓取 (medsci)

## 站点

- 站点页面：https://www.medsci.cn/guideline — 梅斯医学（Medsci）指南库，收录医学指南/共识，按科室分类
- 分类接口：`medsci.cn/medsciCommon/index/columnList`（GET），响应 JSON 的 `data.categoryDtos` 返回分类列表（`categoryId` / `categoryName` / `tenant`）
- 列表接口：`medsci.cn/guideline/search`（GET，参数 `page` / `s_id` / `tenant`），响应为 HTML，分页信息在 `span.page-info-right`（如「页码: 2/34页」）

## 展示特性

- **两级数据流**：先拉分类列表接口，再按「分类 × 页码」二维遍历抓取列表页——分类维度由站点接口动态给出，无需本地维护分类配置
- **分类维度断点续爬**：`{cat_idx, page}` JSON 序列化写入 Redis，任意中断都能从「分类索引 + 页码」精确恢复
- **反爬感知设计**（本示例亮点）：站点连续约 25 次请求后会触发腾讯滑块验证码——**响应仍为 200 但解析无数据**，仅凭状态码无法识别；代码通过「每页 5 秒限速」降低触发概率，并在 README 中说明触发后的处理方式（浏览器过验证 + 更新 Cookie）
- **单页多项解析**：`journal-item` 卡片提取标题 / URL / 指南类型 / 二级类型 / 发布时间 / 发表机构 / 概述，自动补「科室」与「科室id」维度
- **分类失败页上下文重试**：失败页记录 `(page, cat_id, tenant)` 完整上下文，重试时通过 `category_map` 还原分类名；主流程后最多 3 轮重试
- **防御性跳过**：单个分类请求连续失败时先记录错误页再跳到下一个分类，避免死循环
- **`_id` 按 URL 去重**：`generate_string_id(url)`，重复抓取自动更新

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：分类列表 + 分类遍历分页 + 断点 + 重试 |

## 运行方式

```bash
cd examples/medsci
python spider.py
```

## 前置条件

- 无需登录：分类接口用空 Cookie 即可；列表页 `self.cookies` 已清空，留空即可运行
- 若触发腾讯滑块验证码（连续请求约 25 次后，响应 200 但解析无数据）：在浏览器中完成验证后，把新 Cookie（含 `JSESSIONID` 等）填入 `self.cookies`
- 依赖服务：
  - **Redis**：断点续爬（`medsci_log_page` 存 JSON 断点 / `medsci_error_page_set` 存错误页）
  - **MongoDB**：结果存储，collection 名 = 目录名 `medsci`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ 读取序列化断点 {cat_idx, page}
 ├─ get_category_list()                # GET columnList 分类接口
 ├─ for cat_idx in [start_cat_idx..]
 │   └─ while True
 │       ├─ get_list_page(page, cat_id, tenant)   # GET search?page&s_id&tenant
 │       ├─ parse_html_data()         # journal-item 卡片解析 + 总页数
 │       ├─ save_result()             # MongoDB upsert（_id = url MD5）
 │       ├─ record_string({cat_idx, page+1})   # 每页后落盘断点
 │       └─ page >= total_page 时 break，失败则记录错误页后跳过分类
 ├─ 主流程结束，log_page 清空
 └─ 最多 3 轮 handle_error_page(category_map) 重试失败页
```

数据流向：分类接口 JSON → 按分类 GET 列表 HTML → `journal-item` 卡片字段提取（带科室维度）→ MongoDB（`_id = url MD5`）。

## 核心代码片段

**分类列表接口 → 分类映射**（分类数据由站点动态下发，无需本地配置）：

```python
res_json = response.json()
category_list = res_json.get("data", {}).get("categoryDtos", [])
category_map = {c["categoryId"]: c for c in category_list}
```

**卡片解析**（类型标签、日期、机构、概述的容错提取）：

```python
title_tag = item.find("a", class_="ms-link")
span = title_tag.find("span")
title = span.get_text(strip=True) if span else title_tag.get_text(strip=True)
type_spans = item.find_all("span", class_="item-label")
primary_type = type_spans[0].get_text(strip=True) if len(type_spans) > 0 else ""
more_detail = item.find("div", class_="more-detail")
date_span = more_detail.find("p").find("span") if more_detail else None
```

**反爬限速 + 失败跳分类**（避免无限循环和验证码触发）：

```python
page += 1
time.sleep(5)          # 每页限速，降低滑块验证码触发概率
...
else:
    self.error_page_set.add_to_set(self._encode_cache(page_info))
    page += 1
    time.sleep(5)
    break              # skip to next category, avoid infinite loop
```

**JSON 断点落盘**（每页后写入，中断精确恢复）：

```python
self.log_page.record_string(
    json.dumps({"cat_idx": cat_idx, "page": page + 1})
)
```

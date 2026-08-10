# 医脉通指南抓取 (medlive_guide)

## 站点

- 站点页面：https://guide.medlive.cn — 医脉通临床指南库，按科室分类收录国内外临床指南
- 列表接口：https://guide.medlive.cn/more_filter（POST 表单），响应 JSON 的 `data` 字段内嵌一段 HTML 片段（指南列表），`has_more` 标记是否还有下一页
- 分类体系：一级科室（内科、外科…）→ 二级科室（心血管内科、神经内科…），分类值在 `category_list.json` 中维护

## 展示特性

- **POST 表单 + 内嵌 HTML 解析**：向 `more_filter` 提交 `category` / `category_sec` / `page` / `page_size` / `_token` 表单，从 JSON 的 `data` 字段取 HTML 再交给 BeautifulSoup 解析
- **同一站点两种列表策略**（本目录两个文件互为对比）：
  - `spider.py`：单分类全量模式，不过滤科室，一次抓全站
  - `spider_type.py`：分类树遍历模式，按「一级科室 → 二级科室」逐分类抓取，数据带科室维度
- **分类树双重循环遍历**：从 `category_list.json` 读取 `type_value` / `sub_category_list`，两层循环 + 页内 `has_more` 循环
- **序列化断点续爬**（spider_type.py 亮点）：将 `{cat_idx, sec_idx, page}` 三元组 JSON 序列化写入 Redis，任意中断都能从「分类索引 + 二级分类索引 + 页码」精确恢复
- **错误页上下文重试**：失败时把分类值、分类名与页码一起序列化入 Set，重试时能还原完整请求上下文；接口返回 `code=500` 且提示「没有更多数据」时视为分类自然结束，从错误集移除而不重试
- **`_id` 按 URL 去重**：`generate_string_id(url)` 生成 MongoDB `_id`，重复抓取自动更新
- **字段清洗**：发布时间取 `guideBtmTime` 并剥离「发布」后缀，浏览量取 `guideBtmNum` 并剥离「人」后缀

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 单分类全量模式：从第 1 页起抓全部指南列表 |
| `spider_type.py` | 分类树遍历模式：双重循环 + 序列化断点 |
| `category_list.json` | 科室分类树数据（一级科室 → 二级科室），spider_type.py 的运行时依赖 |

## 运行方式

```bash
cd examples/medlive_guide
python spider.py          # 单分类全量模式

python spider_type.py     # 分类树遍历模式（需 category_list.json 同目录）
```

## 前置条件

- **必须填写会话凭证**：站点有 PHP/Laravel 反爬（表单校验 `_token`，会话 cookie `PHPSESSID` / `XSRF-TOKEN` / `laravel_session`）。代码中均已清空：
  - 从浏览器打开 https://guide.medlive.cn/guide/filter，DevTools 取有效 cookie 填入 `self.cookies`
  - 再取页面中的 `csrf_token`（或请求中的 `_token` 值）填入 `self.csrf_token`
- 追踪类 cookie（百度统计 / Matomo `_pk_*`）留空即可
- `spider_type.py` 运行时必须存在 `category_list.json`（本目录已提供）
- 依赖服务：
  - **Redis**：断点续爬（spider.py 用 `..._log_page`；spider_type.py 用 `..._log_page_type` / `..._error_page_set_type`，二者 key 隔离互不影响）
  - **MongoDB**：结果存储，collection 名 = 目录名 `medlive_guide`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

**spider.py（单分类全量）**：

```
run_all()
 ├─ 从 log_page 恢复页码
 ├─ while has_more == "Y"
 │   ├─ get_list_page(page)        # POST more_filter（sub_type=0, category=0…）
 │   ├─ parse_html_data(data)      # BeautifulSoup 解析内嵌 HTML
 │   ├─ save_result()              # MongoDB upsert（_id = url MD5）
 │   └─ log_page.record_int(page)
 ├─ 主流程结束，log_page 清空
 └─ 最多 3 轮 handle_error_page() 重试失败页
```

**spider_type.py（分类树遍历）**：

```
run_all()
 ├─ 读取序列化断点 {cat_idx, sec_idx, page}
 ├─ for cat_idx → category_list[]
 │   └─ for sec_idx → sub_category_list[]
 │       └─ while has_more == "Y"
 │           ├─ get_list_page(page, cat_val, sec_val)
 │           ├─ parse_html_data(..., cat_name, sec_name)   # 数据带科室维度
 │           ├─ save_result()
 │           └─ 每页后 record_string({cat_idx, sec_idx, page+1})
 ├─ 主流程结束，log_page 清空
 └─ 最多 3 轮重试（错误集含分类上下文）
```

数据流向：POST 表单 → JSON `data` 字段 HTML 片段 → BeautifulSoup 提取 标题/URL/机构/发布时间/浏览量 → MongoDB。

## 核心代码片段

**内嵌 HTML 解析**（guideItem 卡片结构 → 结构化记录）：

```python
for a_tag in soup.find_all("a", href=True):
    href = a_tag["href"].strip()
    url = href if href.startswith("http") else self.base_url + href
    guide_item = a_tag.find("div", class_="guideItem")
    if not guide_item:
        continue
    title_div = guide_item.find("div", class_="guideTitle")
    ...
    btm_info = guide_item.find("div", class_="guideBtmInfo")
    if btm_info:
        time_span = btm_info.find("span", class_="guideBtmTime")
        publish_time = time_span.get_text(strip=True) if time_span else ""
        if "发布" in publish_time:
            publish_time = publish_time.split("发布")[0].strip()
    guidelines.append({"_id": generate_string_id(url), "标题": title, ...})
```

**序列化断点**（分类遍历的精确恢复点）：

```python
self.log_page.record_string(json.dumps({
    "cat_idx": cat_idx,
    "sec_idx": sec_idx,
    "page": page + 1 if has_more == "Y" else 1
}))
```

**自然结束识别**（接口用 500 + 文案表达“该分类没有数据了”，不视为错误）：

```python
elif res_json and str(res_json.get("code")) == "500" and "没有更多数据" in res_json.get("msg", ""):
    self.error_page_set.remove_from_set(error_key)
```

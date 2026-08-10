# 药智网临床指南抓取 (yaozh_pharma)

## 站点

- 站点页面：https://db.yaozh.com/cpg — 药智网（yaozh）临床诊疗指南数据库，收录国内外临床指南/共识
- 列表接口：`db.yaozh.com/cpg`（GET），参数为 `p`（页码）+ `source=sitemap`（来源标记），响应为 HTML 表格
- 数据需登录后可见：站点要求已登录的会话 Cookie（`PHPSESSID` / `yaozh_user` 等）

## 展示特性

- **登录会话依赖站点**：代码中所有会话 Cookie 已清空（原实现包含完整的 `PHPSESSID` / `yaozh_user` / `kztoken` 等登录态），运行前需从浏览器登录后自行填入 `self.cookies`
- **分页信息从 HTML 数据属性读取**：`div[data-widget=dbPagination]` 的 `data-total` / `data-size` 直接给出总数与每页条数，用 `math.ceil(total / size)` 计算总页数——无需解析分页导航链接
- **动态终止条件**：`max_page` 首屏解析后锁定，`page >= max_page` 时结束翻页
- **表格行结构解析**：每行 `<th>` 为发布时间（年份），4 个 `<td>` 依次为 题目（含链接）/ 来源 / 指南制定机构 / 求助全文链接；题目链接相对路径自动补全
- **页间限速**：`time.sleep(5)` 降低对站点的请求频率（防触发风控）
- **Redis 断点续爬**：`log_page` 记录已抓页码，中断后从当前页恢复；`error_page_set` 收集失败页并循环重试
- **`_id` 降级策略**：优先取题目链接 MD5，链接缺失时回退为题目文本 MD5

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：分页抓取、表格解析、入库、断点与重试 |

## 运行方式

```bash
cd examples/yaozh_pharma
python spider.py
```

## 前置条件

- **必须填写登录会话**：站点数据接口需要登录态。用浏览器登录 https://db.yaozh.com 后，在 DevTools → Application → Cookies 中复制 Cookie（至少含 `PHPSESSID`、`yaozh_user`、`yaozh_userId`），填入 `self.cookies`
- 依赖服务：
  - **Redis**：断点续爬（`yaozh_pharma_log_page` / `yaozh_pharma_error_page_set`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `yaozh_pharma`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ 从 log_page 恢复页码
 ├─ while True
 │   ├─ get_list_page(page)           # GET ?p={page}&source=sitemap
 │   ├─ extract_cpg_data(html)        # 数据属性算 max_page + 表格行解析
 │   ├─ save_result()                 # MongoDB upsert（_id = 题目链接 MD5）
 │   ├─ log_page.record_int(page)     # 页码落盘
 │   └─ page >= max_page 时 break
 ├─ 主流程结束，log_page 清空
 └─ handle_error_page() 循环重试失败页，直到 Set 清空
```

数据流向：GET 列表 HTML → `data-widget=dbPagination` 数据属性计算总页数 → `<th>` 年份 + 4 个 `<td>` 字段提取 → MongoDB（`_id = 题目链接 MD5`）。

## 核心代码片段

**分页信息从 HTML 数据属性读取**（无需解析分页导航结构）：

```python
pagination = soup.find('div', {'data-widget': 'dbPagination'})
if pagination:
    total = int(pagination.get('data-total', 0))
    size = int(pagination.get('data-size', 20))
    max_page = math.ceil(total / size) if size > 0 else 0
```

**表格行解析**（`<th>` 年份 + 4 个 `<td>` 字段，链接相对路径补全）：

```python
th_tag = row.find('th')
pub_year = th_tag.get_text(strip=True)
cells = row.find_all('td')
title_tag = cells[0].find('a')
title = title_tag.get_text(strip=True) if title_tag else ''
title_link = title_tag.get('href', '') if title_tag else ''
if title_link and not title_link.startswith('http'):
    title_link = self.base_url + title_link
source = cells[1].get_text(strip=True)
organization = cells[2].get_text(strip=True)
```

**动态终止条件**（max_page 首屏锁定后驱动翻页）：

```python
if max_page is None:
    max_page = parsed_data.get('max_page', 0)
    self.log_print.print(f"解析到共有最大分也: {max_page}")
...
self.log_page.record_int(page)
if max_page is not None and page >= max_page:
    break
page += 1
```

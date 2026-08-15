# BlackRock 投资研究院全球洞察抓取 (blackrock_insights)

单页静态 HTML 归档页抓取示例：无需登录、无分页接口、无加密参数，一次 GET 即可拿到全部洞察条目。

## 站点

- 站点页面：https://www.blackrock.com/corporate/insights/blackrock-investment-institute/archives — BlackRock 投资研究院（BII）全球洞察归档页，覆盖其发布的市场评论、投资展望等英文研究报告
- 数据形态：归档页为**单页静态 HTML**，一次返回全部洞察条目（无分页接口），无需登录
- 详情链接：卡片内为相对 href，`urljoin` 补全为完整 `https://www.blackrock.com/...` 地址（写入 `file_url` 字段）

## 展示特性

- **静态 HTML 单页抓取**：`SingleRequestHandler` GET 归档页直接解析，`check_size=False` 不做体积校验
- **BeautifulSoup 卡片解析**：`div.gls-related-literature div.item` 卡片中提取 标题（`h2`）/ 日期（`div.attribution`）/ 链接（`a`）/ 摘要（`div.description`）
- **英文日期标准化**：`convert_date_robust`（`jimmyspider.datetime_utils`）将 `December 31, 2025` 等任意英文日期格式统一转为 `YYYY-MM-DD`
- **相对链接补全**：`urljoin(self.archives_url, href)` 拼接详情页完整 URL
- **`_id` 降级策略**：`generate_string_id(url or title)` — 详情 URL 缺失时回退为标题文本 MD5
- **Redis 完成标记**：`log_page.record_string({"done": true})`，避免重复采集同一归档页
- **完整浏览器头伪装**：`sec-ch-ua` / `referer` / `user-agent`（Chrome 147 / Linux）全套请求头
- **编码自适应**：`response.apparent_encoding` 推断归档页编码，规避乱码
- **入口零配置**：`kwargs.setdefault("pro_path", Path(__file__).parent)` 自动定位项目目录，`test_url` 自动装配（用于 `SingleRequestHandler` 连通性探测）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：GET 归档页 → 卡片解析 → 字段标准化 → `save_result` 入库 |

## 运行方式

```bash
cd examples/blackrock_insights
python spider.py
```

## 前置条件

- 无需登录 cookie；直接 GET 归档页即可
- 依赖服务：
  - **Redis**：完成标记（key `blackrock_insights_log_page`，`{table_name}_log_page` 模式）
  - **MongoDB**：结果存储，collection 名 = 目录名 `blackrock_insights`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

## 爬虫架构

```
run_all()
 ├─ get_archives_page()          # SingleRequestHandler GET 归档页，编码按 apparent_encoding
 ├─ parse_archives(html_text)    # 遍历 div.gls-related-literature div.item 卡片
 │   ├─ h2                 → 标题
 │   ├─ div.attribution    → convert_date_robust → 发布时间 (YYYY-MM-DD)
 │   ├─ a[href]            → urljoin → file_url
 │   ├─ div.description    → 概述
 │   └─ 组装 {_id, 标题, 发布时间, file_url, 概述, create_time}
 ├─ save_result(insert_list=...) # 按 _id upsert 写入 MongoDB
 └─ log_page.record_string({"done": True})   # 完成标记，去重核心
```

数据流向：归档页 HTML → 卡片条目（标题/发布时间/详情链接/概述）→ MongoDB `blackrock_insights`（`save_result` 内部按 `_id` upsert，重复运行只覆盖不新增）。

## 核心代码片段

**归档页抓取**（请求头 + 编码自适应）：

```python
def get_archives_page(self) -> Optional[str]:
    response = self.single_fetcher.fetch(
        self.archives_url,
        headers=self.headers,
        method="GET",
        check_size=False,
    )
    if response and response.status_code == 200:
        response.encoding = response.apparent_encoding
        return response.text
    return None
```

**卡片级解析与 `_id` 降级**（标题与链接任一缺失都不影响入库）：

```python
title_tag = item.select_one("h2")
title = title_tag.get_text(strip=True) if title_tag else ""
link_tag = item.select_one("a")
href = link_tag.attrs.get("href") if link_tag else ""
url = urljoin(self.archives_url, href) if href else ""

results.append({
    "_id": generate_string_id(url or title),   # URL 缺失回退标题 MD5
    "标题": title,
    "发布时间": publish_time,
    "file_url": url,
    "概述": description,
    "create_time": now_ts,
})
```

**英文日期标准化**（任意格式 → YYYY-MM-DD）：

```python
from jimmyspider.datetime_utils import convert_date_robust

date_tag = item.select_one("div.attribution")
date_str = date_tag.get_text(strip=True) if date_tag else ""
publish_time = convert_date_robust(date_str) if date_str else ""
```

**完成标记写入**（再次运行即可跳过）：

```python
def _encode_cache(self, value: Dict) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True)

self.log_page.record_string(self._encode_cache({"done": True}))
```

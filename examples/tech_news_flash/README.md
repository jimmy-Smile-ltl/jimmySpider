# 科技快报网新闻抓取 (tech_news_flash)

按文章编号逐条抓取中国科技产业快报网站（citreport.com）的新闻详情页（GBK 编码），结构化提取标题/日期/分类/发布者/正文/图片后写入 MySQL。迁移自北大信研院 pro1「科技快报网」，去掉了 HDFS 图片上传与硬编码数据库配置。

## 站点

- 站点页面：https://www.citreport.com/ — 中国科技产业新闻快报网站
- 详情页 URL：`https://www.citreport.com/news/{编号}-1.html`，编号递增排列，历史最大编号约 198327
- 页面编码：GBK（响应需强制 `res.encoding = "gbk"`）

## 展示特性

- **编号递增式抓取**：无需列表页，直接从断点编号逐条抓详情，天然不重复、无分页
- **优雅终止条件**：编号越过历史最大值（>198327）后，连续失败 10 次或已追到最近 3 天内的文章即停止
- **删除文章检测**：页面包含 `#messagetext` 即视为「文章不存在或已被删除」，只记失败不计入数据
- **失败重试 + 延迟**：单条请求最多重试 5 次，失败间隔随机 sleep 1~3 秒
- **字段解析**：标题（`h1.ph`）、日期（`div.cl > p > span`，兼容 `YYYY-MM-DD HH:MM:SS` 与 `YYYY-MM-DD`）、分类（`div.portal_sort > ul > li`）、发布者（`div.contacts > span`，冒号后取真值）、正文（`#article_content`）、图片（同容器内 `img` 且 src 以 http 开头）
- **断点续爬**：成功页记录 `tech_news_flash_log_page`，失败页写入 `tech_news_flash_error_pages`
- **自动建表 + upsert**：`article_url` 唯一键，重复运行不产生重复数据

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件） |

## 运行方式

```bash
cd examples/tech_news_flash
python spider.py
```

## 前置条件

- **MySQL**：`db_type="mysql"`（代码内置），连接参数见根目录 `jimmyspider.yaml`
- **Redis**：断点缓存（`tech_news_flash_log_page` 页码、`tech_news_flash_error_pages` 失败列表）
- 可选代理：`jimmyspider.yaml` 配置 `proxy_tunnel_url` 后自动走隧道代理

## 爬虫架构

```
run()
 ├─ start_page = log_page.get_int()，先自增再抓取（首次从编号 1 开始）
 ├─ while True:
 │   ├─ handle_one_article(start_page)
 │   │   ├─ 构造 {base_url}/news/{N}-1.html
 │   │   ├─ fetch()：GBK 解码，失败重试 5 次（间隔 1~3s 随机）
 │   │   ├─ 检测 #messagetext → 文章不存在，返回失败
 │   │   ├─ extract_article 解析 6 个字段 → save_result upsert
 │   │   └─ 成功 → record_int 写断点；失败 → error_pages 记入
 │   └─ 结束条件：N > 198327 且（连续失败 >= 10 次 或 最近成功日期距今天 < 3 天）
```

数据流向：详情页 HTML（GBK）→ 6 字段新闻对象 → MySQL 单表（`tech_news_flash`）；Redis 维护编号游标与失败集合。

## 核心代码片段

**强制 GBK 解码 + 重试**：

```python
def fetch(self, url: str):
    for _ in range(5):
        try:
            res = self.single_fetcher.fetch(url, headers=self.headers)
            if res:
                res.encoding = "gbk"   # 站点为 GBK 编码
                return res
        except Exception as e:
            self.log_print.error(f"请求失败: {e}")
        time.sleep(random.uniform(1, 3))
    return None
```

**终止条件（防爬策略核心）**：

```python
# 超过历史最大编号，且连续失败 10 次或已追到最近 3 天内的文章
if start_page > 198327:
    if fail_count >= 10 or (record_date and (datetime.now() - record_date).days < 3):
        self.log_print.info("连续失败超过 10 次或已追到最新文章，结束爬取")
        break
```

**详情字段提取**：

```python
return {
    "article_url": url,
    "title": self.extract_text(soup, "h1.ph"),
    "date": parse_date(self.extract_text(soup, "div.cl > p > span")),
    "categories": self.extract_list(soup, "div.portal_sort > ul > li"),
    "publisher": publisher,          # div.contacts > span，冒号后取真值
    "content": self.extract_text(soup, "#article_content"),
    "image_urls": self.extract_image_urls(soup, "#article_content img"),
}
```

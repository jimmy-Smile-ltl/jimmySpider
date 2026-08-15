# 机器人实验室博客抓取 (robot_lab)

抓取 UC Berkeley BAIR（Berkeley AI Research）实验室博客的技术文章，分页抓列表、并发抓详情，结构化提取标题/作者/关键词/摘要/正文/图片后写入 MySQL。迁移自北大信研院 pro37「机器人 UC Berkeley BAIR」。

## 站点

- 站点页面：https://bair.berkeley.edu/blog — BAIR 博客，机器人 / AI 领域高质量技术文章
- 列表页：第 1 页无后缀，之后为 `page{N}` 翻页（`https://bair.berkeley.edu/blog/page2`）
- 详情页：`/blog/{文章 slug}`，正文在 `article.post-content`

## 展示特性

- **下一页按钮判定末页**：`div.right > a.pagination-item` 存在且无 `.disabled` 类 → 有下一页
- **详情并发抓取**：`async_fetcher.fetch_all` 一次性异步抓取整页文章详情，失败的文章原样入库不丢弃
- **结构化字段**：标题、作者（`span.post-meta a`）、关键词（`meta[name='keywords']` 逗号拆分）、摘要（卡片内直接子 `<p>` 拼接）、发布日期、正文、正文内图片链接（相对路径自动补全协议头）
- **断点续爬**：页码记录于 Redis `log_page_robot_lab`；表内残留测试数据（<20 条）自动删表重建并重置断点
- **自动建表 + upsert**：`article_url` 唯一键，重复运行自动 upsert 不产生重复数据

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件，列表 + 详情一体） |

## 运行方式

```bash
cd examples/robot_lab
python spider.py
```

## 前置条件

- **MySQL**：`db_type="mysql"`，连接参数见根目录 `jimmyspider.yaml`
- **Redis**：断点缓存（key `log_page_robot_lab`）
- 无需登录与代理，直连即可

## 爬虫架构

```
run()
 ├─ start_page = log_page_robot_lab.get_int(default=1)
 ├─ while True:
 │   ├─ get_page(page_num)：第 1 页直连，之后 {site}/page{N}
 │   ├─ extract_page_articles：
 │   │   ├─ 解析 div.posts > div.post 卡片（标题/链接/作者/关键词/摘要/日期）
 │   │   └─ 判定 div.right > a.pagination-item(.disabled) 是否有下一页
 │   ├─ parse_article_detail：async_fetcher.fetch_all 并发抓详情
 │   │   ├─ article.post-content → content + img_url（相对 URL 补全）
 │   │   └─ 失败文章原样入库
 │   ├─ save_result(article_list)  # article_url 唯一键 upsert
 │   ├─ 无数据或已到末页 → break
 │   └─ start_page += 1，record_int 写断点
```

数据流向：列表页 HTML → 卡片基础信息 → 详情页 HTML → 正文/图片补全 → MySQL 单表（`robot_lab`）。

## 核心代码片段

**分页 URL 构造 + 末页判定**：

```python
def get_page(self, page_num: int):
    page_url = self.site if page_num == 1 else f"{self.site}/page{page_num}"
    return self.single_fetcher.fetch(page_url, headers=self.headers)

next_button = soup.select_one("div.right > a.pagination-item")
next_disable_button = soup.select_one("div.right > a.pagination-item.disabled")
if not next_disable_button and next_button:
    has_next = True
```

**详情并发抓取（失败不丢弃）**：

```python
response_dict = self.async_fetcher.fetch_all(
    url_list=[article["article_url"] for article in articles],
    headers=self.headers,
)
for article in articles:
    response_text = response_dict.get(article["article_url"])
    if not response_text:
        article_insert_list.append(article)   # 请求失败 → 原样入库
        continue
    article["img_url"] = extractSoup.extract_urls_relativeURL(
        soup=content_div, selector="img", relative_url=article["article_url"]
    )
    article["content"] = extractSoup.extract_content(content_div)
```

**关键词与发布日期解析**：

```python
keywords_tag = post.select_one("meta[name='keywords']")
keywords = keywords_tag.attrs.get("content", "").split(",") if keywords_tag else []
date_published = HandleDatetime.convert_date_robust(
    extractSoup.extract_text(soup=post, selector="span.post-meta:nth-child(2)")
)
```

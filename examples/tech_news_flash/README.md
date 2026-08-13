# 科技快报网新闻抓取 (tech_news_flash)

## 站点

- 站点页面：https://www.citreport.com/ — 中国科技产业新闻快报网站，按文章编号（如 `/news/198327-1.html`）递增排列
- 页面编码：GBK

## 展示特性

- **编号递增式抓取**：从 Redis 断点页开始，逐条抓取新闻详情页，无需列表页
- **优雅终止条件**：连续失败 10 次（文章编号已越界）或已追到最近 3 天内的文章时自动停止
- **字段解析**：标题、日期、分类、发布者、正文、图片链接，分类/图片以 JSON 存入 MySQL
- **断点续爬**：成功页记录 `tech_news_flash_log_page`，失败页写入 `tech_news_flash_error_pages`
- **自动建表**：`article_url` 唯一键，重复运行自动 upsert 不产生重复数据

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
- **Redis**：断点续爬（key 前缀 `tech_news_flash_`）
- 可选代理：`jimmyspider.yaml` 配置 `proxy_tunnel_url` 后自动走隧道代理

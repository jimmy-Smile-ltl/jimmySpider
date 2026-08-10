# hello_world —— jimmySpider 入门示例

> 本示例是框架**最简可用爬虫**：约 50 行代码（含注释），不涉及代理、断点续爬、反爬对抗。
> 新用户建议从这里开始，理解「继承基类 → 发请求 → 解析 → 入库」的最小闭环。

## 站点

- https://news.ycombinator.com/ — Hacker News 首页，纯 HTML，无需登录、无复杂反爬，适合入门
- 页面结构：每条新闻是一个 `tr.athing`，标题位于 `span.titleline > a`（文本 + href）

## 它演示了什么

1. **继承 `JimmySpider` 基类**：传入 `pro_path` 后基类自动装配组件
   - `log_print` 日志、`db_manager` MongoDB、`extract_soup` HTML 解析、`single_fetcher` 请求处理器等
2. **`SingleRequestHandler`**：`fetch(url)` 发送 GET 请求，自带重试，失败返回 `None`
3. **`extractSoup` 解析 HTML**：`extract_texts()` 批量取标题文本、`extract_list_url()` 批量取链接
4. **`save_result()` 入库**：列表按 `_id` 批量 upsert 到 MongoDB（collection = 目录名 `hello_world`）
5. **`generate_string_id()`**：对链接取 MD5 作为 `_id`，作为去重/更新的唯一键

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件，唯一代码文件） |
| `README.md` | 本文件 |

## 运行方式

```bash
cd examples/hello_world
python spider.py
```

## 前置条件

- 已安装 `jimmyspider` 框架（`pip install -e .` 或 `pip install -e .`）及 `beautifulsoup4`
- 本地 MongoDB（默认 `mongodb://localhost:27017`，库 `jimmyspider`）—— 结果保存地
- 无需 Redis（本示例不演示断点续爬）、无需登录、无需代理
- 网络可达 Hacker News；如无法访问，把 `self.list_url` 换成任意简单网站即可

## 代码解读

与 `spider.py` 顺序一一对应：

1. 继承 `JimmySpider` → 基类自动装配全部组件
2. `single_fetcher.fetch(list_url)` → 拿到响应（失败返回 None）
3. `BeautifulSoup(response.text)` → 构建 HTML 解析对象
4. `extract_soup.extract_texts / extract_list_url` → 提取标题与链接
5. 组装 `{"_id", "标题", "链接"}` 列表
6. `save_result()` → MongoDB 按 `_id` upsert

## 下一步

- 想看分页 + Redis 断点续爬 → [eastmoney_report](../eastmoney_report/README.md)
- 想看 JSON POST API + 字段映射 → [moj_regulations](../moj_regulations/README.md)
- 想看金融数据 + 并发详情抓取 → [twse_taiwan](../twse_taiwan/README.md)

# Frontiers 科技文献抓取 (tech_literature)

## 站点

- 站点页面：https://www.frontiersin.org/journals — Frontiers 出版集团学术期刊，约 288 本（含合作伙伴期刊）
- 数据接口：`/api/v3/journals/search/*`（期刊列表 / 文章检索，POST JSON）

## 展示特性

- **三级抓取架构**：期刊列表 → 单期刊分页文章列表 → 文章详情页（AsyncRequestHandler 并发）
- **期刊断点续爬**：当前期刊 / 期刊内页码 / 已抓数量 / 已完成期刊列表四个 Redis key，中断后原样恢复
- **多域名 API 自动适配**：通过期刊主页重定向域名判断使用 `frontiersin.org` / `frontierspartnerships.org` / `ebm-journal.org` 的 API，未知域名按 host 拼接
- **结构化文献字段**：正文、摘要、关键词、作者、审稿人、编辑、通讯作者（Base64 邮箱解码）、参考文献、接收/录用/发布日期、PDF 链接
- **PDF-only 文章兼容**：无正文页的文章直接以列表链接作为 PDF 链接
- **期刊信息本地缓存**：首次抓取后写入 `journal_info.json`，后续运行免重复请求

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件） |
| `journal_info.json` | 期刊信息缓存（首次运行自动生成） |

## 运行方式

```bash
cd examples/tech_literature
python spider.py
```

## 前置条件

- 无需登录与代理，直连即可
- **MySQL**：`db_type="mysql"`，连接参数见根目录 `jimmyspider.yaml`；测试残留（<100 条）自动清表并重置断点
- **Redis**：断点续爬（key 前缀 `tech_literature_`）

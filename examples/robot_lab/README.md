# 机器人实验室博客抓取 (robot_lab)

## 站点

- 站点页面：https://bair.berkeley.edu/blog — UC Berkeley BAIR 实验室（Berkeley AI Research）博客，机器人 / AI 领域高质量技术文章

## 展示特性

- **分页判定**：列表页 `page{N}` 翻页，通过"下一页"按钮的 disabled 状态判断是否到达末页
- **详情并发抓取**：AsyncRequestHandler 异步抓取整页文章详情，失败的文章原样入库不丢弃
- **结构化字段**：标题、作者、关键词、摘要、发布日期、正文、正文内图片链接（相对路径自动补全协议头）
- **断点续爬**：页码记录于 `robot_lab_log_page`；表内残留测试数据（<20 条）自动删表重建并重置断点
- **自动建表**：`article_url` 唯一键，重复运行自动 upsert

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件） |

## 运行方式

```bash
cd examples/robot_lab
python spider.py
```

## 前置条件

- 无需登录与代理，直连即可
- **MySQL**：`db_type="mysql"`，连接参数见根目录 `jimmyspider.yaml`
- **Redis**：断点续爬（key 前缀 `robot_lab_`）

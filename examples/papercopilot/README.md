# Paper Copilot 论文爬虫 (papercopilot)

从盛大网络 `pro3_papercopilot_com` 迁移，两个文件组成"抓论文 → 补作者"两阶段流水线。

## 站点

- 站点：https://papercopilot.com — 学术会议论文聚合站（AI/ML 顶会为主）
- 反爬：无签名鉴权；论文列表是 AJAX 分批加载，直到返回字符串 `"0"` 才结束

## 展示特性

- **AJAX 批量接口逆向**：论文列表由 JS 的 `loadMoreRows` 通过
  `/wp-admin/admin-ajax.php?action=load_paperlist&batch=N` 分批 append，
  每批约 1500 条，返回 `"0"` 结束（batch 0 是默认 100 条）
- **表头动态对齐解析**：th 与 td 一一对应，按表头名分派解析器
  （Title/Authors/Affiliation/Citation...，`R#` 排序列跳过）
- **会议清单缓存**：首次从首页解析三级菜单（分类 → 会议 → 年份）存 json，之后读缓存
- **多层 Redis 断点**：分类 / 会议 / 年份三级去重 + 当前会议标记，中断可续
- **PostgreSQL 批量 upsert**（article_url 唯一键）
- **跨示例流水线**：`get_author_from_paper_copilot.py` 读取上表 authors JSONB，
  sys.path 复用 `google_scholar/` 示例的类回查作者（动态批量线程池 + id 偏移断点）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_papercopilot.py` | 主爬虫：三层遍历会议清单 → 逐年 AJAX 分批抓取 → 入库 |
| `get_author_from_paper_copilot.py` | 流水线第二阶段：读表 → 提取 scholar_id → 回查 GS 作者 → 写 `scholar_author` |

## 运行方式

```bash
cd examples/papercopilot
python spider_papercopilot.py           # 第一步：抓全站会议论文（全量约 60 万条，量力运行）
python get_author_from_paper_copilot.py # 第二步：补作者信息（依赖 google_scholar 示例）
```

## 前置条件

- PostgreSQL（`db_type="postgresql"`）+ Redis（断点缓存）；`pip install psycopg2-binary`
- 第二步要求 `examples/google_scholar/` 目录存在（运行时通过 sys.path 引入）
- 第一步抓完才有数据可处理；也可手动改 `table_name_read` 指向其他含 authors 的表

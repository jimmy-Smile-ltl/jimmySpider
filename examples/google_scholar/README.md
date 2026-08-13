# Google Scholar 作者信息流水线 (google_scholar)

从盛大网络 `pro2_google_scholar` 迁移。四个文件组成"按标题搜论文 → 抓作者主页 → 合作者扩散"作者库构建流水线。

## 站点

- 站点：https://scholar.google.com — 全球学术搜索（作者主页 / citations）
- 反爬：无签名鉴权，但风控严格——频率高触发"请进行人机身份验证"（与 IP 质量强相关），
  需 `CurlRequestHandler` 指纹伪装 + 代理 + 严格控制速率

## 展示特性

- **GS 搜索解析**：`get_article_by_title` — 标题搜索、被引用次数、作者列表
  （有/无 GS 主页 URL 分开处理）
- **作者主页全字段抓取**：`get_author_info_by_id` — 学术指标（全部 vs 2020 后）、领域、
  历年引用、文章列表 POST json=1 翻页（引用 <5 停止）、合作者、302 跳转换 ID 处理
- **管道驱动**：`get_author_by_title` — 扫描来源表（默认 arxiv_org 产出）逐条按标题搜索，
  文章入 `article_search_by_google_scholar`（article_url 唯一 + origin 联合唯一），作者入 `scholar_author`
- **合作者扩散**：`expand_author_by_collaborator` — 按领域关键词筛种子作者 → 遍历合作者 → 新作者写回
- **跨示例联动**：arxiv_org / papercopilot 示例的产出表都能接进来补作者

## 文件说明

| 文件 | 说明 |
|------|------|
| `get_article_by_title.py` | 按标题搜索 GS，返回文章 + 作者列表（原 `get_artilce_by_title.py`，方法名已修正） |
| `get_author_info_by_id.py` | 按 scholar_id 抓作者主页全部信息（主页/指标/文章/合作者） |
| `get_author_by_title.py` | 流水线核心：扫描来源表 → 按标题搜文章 → 抓作者 → 双表入库 |
| `expand_author_by_collaborator.py` | 合作者扩散：筛选领域种子 → 回查合作者 → 写回作者表 |

## 运行方式

```bash
cd examples/google_scholar
python get_author_by_title.py                  # 默认处理 article_arxiv_org（先跑 arxiv_org 示例）
python get_author_by_title.py article_paper_copilot  # 指定其他来源表
python expand_author_by_collaborator.py        # 有作者数据后向外扩散一层
python get_author_info_by_id.py                # 单 ID 演示：DTthB48AAAAJ
```

## 前置条件

- PostgreSQL（连接参数在 `jimmyspider.yaml`）+ Redis（断点缓存）；`pip install psycopg2-binary`
- 建议配置代理后运行（GS 对请求频率敏感）
- 依赖 `get_article_by_title.py` / `get_author_info_by_id.py` 同目录存在（sys.path 引入）

# Google Scholar 作者信息流水线 (google_scholar)

「按标题搜论文 → 抓作者主页 → 合作者扩散」三阶段作者库构建流水线，四个文件互相复用（从盛大网络 `pro2_google_scholar` 迁移）。

## 站点

- 站点：https://scholar.google.com — 全球学术搜索，作者主页 / citations 数据
- 关键接口：
  - 搜索：`https://scholar.google.com/scholar?hl=zh-CN&q=...`（HTML 结果页）
  - 作者主页：`https://scholar.google.com/citations?user={id}&hl=zh-CN`
  - 合作者：`/citations?view_op=list_colleagues&user={id}&hl=zh-CN&json=`（一次全量）
  - 文章翻页：`/citations` POST `json=1` + `cstart` 偏移，每页 100 条
- 反爬：无签名鉴权，但风控严格——频率高触发「请进行人机身份验证」（与 IP 质量强相关），需 `CurlRequestHandler` 指纹伪装 + 代理 + 严格控制速率

## 展示特性

- **GS 搜索解析**（`get_article_by_title.py`）：结果列表 `#gs_res_ccl_mid > div.gs_r.gs_or.gs_scl`，提取 标题 / 链接 / 被引用次数（`被引用次数：(\d+)`）/ 发布信息 + 作者列表（有/无 GS 主页 URL 分开处理，`author_dict_list` 带序号）
- **作者主页全字段抓取**（`get_author_info_by_id.py`）：姓名 / 头像 / 主页 / 学术指标（全部 vs 2020 后，`#gsc_rsb_st`）/ 单位 / 领域 / 历年引用（`gsc_md_hist_b`）/ 开放获取数；文章列表 POST `json=1` 翻页（引用数 <5 停止），合作者一次全量
- **302 跳转换 ID**：部分主页重定向（如 user=iBAr8iQAAAAJ 跳新 ID），以最终 `response.url` 的 `user` 为准，两个 ID 均可用
- **Redis 去重**：`log_finished_get_info_by_id` 集合记录已处理 ID，`enforce=True` 可强制刷新
- **管道驱动**（`get_author_by_title.py`）：扫描来源表（默认 arxiv_org 示例产出 `article_arxiv_org`，可用 sys.argv 指定其他表）逐条按标题搜索，文章入 `article_search_by_google_scholar`（`article_url` 唯一 + `(origin_id, origin_table, origin_title, article_idx)` 联合唯一），作者入 `scholar_author`（scholar_id 唯一）
- **合作者扩散**（`expand_author_by_collaborator.py`）：按领域关键词（machine learning / NLP / LLM 等 10 个，大小写不敏感）筛种子作者 → 遍历 `collaborator_list` → 新作者写回 `scholar_author`
- **跨示例联动**：arxiv_org / papercopilot 示例的产出表都能接进来补作者；papercopilot 的 `get_author_from_paper_copilot.py` 也反向复用本示例类

## 文件说明

| 文件 | 说明 |
|------|------|
| `get_article_by_title.py` | 按标题搜索 GS，返回文章 + 作者列表（类 `GetArticleByTitle`，原 `get_artilce_by_title.py`） |
| `get_author_info_by_id.py` | 按 scholar_id 抓作者主页全部信息（类 `GetAuthorInfoById`） |
| `get_author_by_title.py` | 流水线核心：扫描来源表 → 按标题搜文章 → 抓作者 → 双表入库（类 `GetAuthorByTitle`） |
| `expand_author_by_collaborator.py` | 合作者扩散：筛选领域种子 → 回查合作者 → 写回作者表（类 `ExpandAuthorByCollaborator`） |

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
- 依赖同目录其他 py 文件（sys.path 引入）；`curl_cffi` 随框架安装

## 爬虫架构

```
GetAuthorByTitle.run_thread(max_workers=5)          # get_author_by_title.py
 ├─ log_offset_get_author_{table} 断点 → current id
 ├─ 每批 20 条 SELECT id, article_title FROM 来源表
 ├─ ThreadPoolExecutor(5) → handle_one_title_thread()
 │   ├─ GetArticleByTitle.handle_one_title(title)    # 搜索 → 结果列表
 │   ├─ 文章补 origin 元数据 → 批量 upsert article_search_by_google_scholar
 │   ├─ 作者 URL 解析 user 参数 → scholar_id（字典去重）
 │   └─ 并发 GetAuthorInfoById.handle_one_scholar_id() → upsert scholar_author
 └─ 批次间 sleep(5) 限速

GetAuthorInfoById.handle_one_scholar_id(id)         # get_author_info_by_id.py
 ├─ get_home_info(): /citations 主页解析 + 302 换 ID + 首页自带文章
 ├─ get_coauthors(): view_op=list_colleagues 一次全量
 ├─ get_articles(): POST json=1 + cstart 翻页（引用 <5 或不足一页停止）
 └─ log_finished_get_info_by_id 集合去重

ExpandAuthorByCollaborator.run()                    # expand_author_by_collaborator.py
 ├─ 每批 500 条筛 scholar_author（category 关键词匹配 + collaborator_list 非空）
 └─ process_batch(): 遍历合作者 profile_url → user 参数 → handle_one_scholar_id
     → 新作者批量写回 scholar_author（unique_col="scholar_id"）
```

数据流向：来源表标题 → GS 搜索结果 → 文章表 + 作者表 → 合作者扩散 → 作者库持续壮大。

## 核心代码片段

**搜索页作者解析**（有 URL / 无 URL 的作者分开收集）：

```python
author_dict = {}
for link in author_tag.select("a"):
    author_dict[link.get_text().strip()] = urljoin(url, link.get("href"))
author_list = [item.strip() for item in author_tag.get_text()
               .replace("\xa0", " ").split(",")]
article_info["author_dict_list"] = [
    {"name": name, "order": idx + 1, "url": author_dict.get(name, None)}
    for idx, name in enumerate(author_list)
]
```

**文章列表 JSON 翻页**（首屏已含前 20 条，cstart 从 20 起步）：

```python
while True:
    cstart = 20 if page == 0 else page * page_size
    response = self.single_handler.fetch(
        "https://scholar.google.com/citations",
        params={"user": scholar_id, "hl": "zh-CN",
                "cstart": f"{cstart}", "pagesize": f"{page_size}"},
        data={"json": "1"}, method="POST")
    res_json = response.json()
    article_list_more = self.extract_articles(BeautifulSoup(res_json.get("B"), "html.parser"))
    article_list.extend(article_list_more)
    if len(article_list_more) < page_size:      # 不足一页即最后一页
        break
    page += 1
    time.sleep(1)                               # 避免请求过快被封 IP
```

**合作者扩散的领域筛选**（关键词大小写不敏感）：

```python
need_cat = ["Natural Language Processing", "Deep Learning", "Machine Learning",
            "Large Language Models", "Reinforcement Learning", "Algorithms", ...]
lower_need_cat = [cat.lower() for cat in need_cat]
match_flag = any(key.lower() in self.lower_need_cat for key in research_area.keys())
```

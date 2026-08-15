# Paper Copilot 学术会议论文爬虫 (papercopilot)

AJAX 批量接口逆向 + PostgreSQL 存储的会议论文爬虫，由「抓论文 → 补作者」两阶段流水线组成（从盛大网络 `pro3_papercopilot_com` 迁移）。

## 站点

- 站点：https://papercopilot.com — 学术会议论文聚合站（AI/ML 顶会为主）
- 站点层级：分类（AI/ML 等）→ 会议 → 年份 → 论文列表页
- 反爬：无签名鉴权；论文列表由 JS 的 `loadMoreRows` 通过 `/wp-admin/admin-ajax.php?action=load_paperlist&batch=N` 分批 append 加载，每批约 1500 条，返回字符串 `"0"` 结束（batch 0 是默认的 100 条）

## 展示特性

- **AJAX 批量接口逆向**：先访问年份页提取 `var ajaxmeta = {...}`（`script#paperlist.ajax-ts-extra` 内正则），再以 `batch` 递增循环拉取直到响应 `"0"`；`check_size=False / check_status_code=False` 防止 1 字节结束标记被默认校验吞掉
- **表头动态对齐解析**：年份页二级表头 `#paperlist thead tr:nth-child(2)`（先剔除 `#aff_switch` 干扰元素）与每行 td 一一对应，按表头名分派解析器（Title / Authors / Affiliation / Country of Aff. / Citation，`R#` 排序列跳过）
- **单行多字段提取**：作者四类链接（Google Scholar `data-gs` / 主页 `data-hp` / DBLP `data-dblp` / OpenReview `data-or` + 机构/国家索引）、三种引用数（GS 引用 / 评分平均引用 / 评分字符串引用，内嵌 HTML 再解析）、`social_links` 字典
- **会议清单缓存**：首次从首页解析三级菜单（`div.intro-shortcode > table` 分类行 → `ul.top-menu` 会议 → `span.nav-menu-item-inside` 年份）存 `papercopilot_conference_list.json`，之后读缓存；征稿期无 Paper List 链接的年份自动跳过
- **多层 Redis 断点**：已完成分类 / 会议 / 年份三个 list + 当前会议标记，中断可续（key 形如 `log_finished_cat_article_paper_copilot`）
- **PostgreSQL 批量 upsert**：`article_url` 唯一键；表数据过少（`drop_table(max_num=20)`）自动重建并清空全部断点
- **跨示例流水线**：`get_author_from_paper_copilot.py` 读取上表 `authors` JSONB，sys.path 复用 `google_scholar/` 示例的类回查作者（动态批量线程池 + id 偏移断点）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider_papercopilot.py` | 主爬虫（类 `SpiderPaperCopilot`）：三层遍历会议清单 → 逐年 AJAX 分批抓取 → PostgreSQL 入库 |
| `get_author_from_paper_copilot.py` | 流水线第二阶段（类 `GetAuthorPaperCopilot`）：读表 → 解析 authors JSONB → 提取 scholar_id 回查 GS 作者 → 写 `scholar_author` |

## 运行方式

```bash
cd examples/papercopilot
python spider_papercopilot.py           # 第一步：抓全站会议论文（全量约 60 万条，量力运行）
python get_author_from_paper_copilot.py # 第二步：补作者信息（依赖 google_scholar 示例）
```

## 前置条件

- **PostgreSQL**（`db_type="postgresql"`）+ **Redis**（断点缓存）；`pip install psycopg2-binary`
- 第二步要求 `examples/google_scholar/` 目录存在（运行时通过 sys.path 引入）
- 第一步抓完才有数据可处理；也可手动改 `table_name_read` 指向其他含 authors 的表

## 爬虫架构

**第一步（spider_papercopilot.py）**：

```
run()
 ├─ 加载会议清单（缓存 json，无则解析首页三级菜单生成）
 ├─ for cat in conference_list:               # 分类层
 │   ├─ log_finished_cat 去重
 │   ├─ for conf in cat["conf_list"]:         # 会议层
 │   │   ├─ log_finished_conf 去重；log_current_conf 记录当前会议
 │   │   ├─ for year_info in conf["year_url_list"]:   # 年份层
 │   │   │   ├─ handle_one_year()
 │   │   │   │   ├─ visit_home_year() 提取 ajaxmeta + 表头
 │   │   │   │   ├─ batch=0..N 循环调 admin-ajax.php，直到返回 "0"
 │   │   │   │   ├─ 实体反转义 + parse_paper_list_html() 按表头解析
 │   │   │   │   └─ insert_data_list(unique_col="article_url")
 │   │   │   └─ log_finished_year 追加，sleep(5)
 │   │   └─ 会议完成 → log_finished_conf 追加，年份集合清空，sleep(30)
 │   └─ 分类完成 → log_finished_cat 追加，sleep(60)
```

**第二步（get_author_from_paper_copilot.py）**：

```
run_thread_dynamic_batch(max_workers=5, batch_size=40)
 ├─ log_offset_get_author_article_paper_copilot 断点 → current id
 ├─ SELECT id, article_title, authors FROM article_paper_copilot
 │   WHERE id BETWEEN current AND current+39 AND year::integer > 2019
 ├─ ThreadPoolExecutor 动态批量：先填满 max_workers，任务完成再补充
 │   └─ handle_one_article()
 │       ├─ authors 字段 json.loads（JSONB 子元素）
 │       ├─ urlparse 提取 author_url_googlescholar 的 user 参数 → scholar_id
 │       ├─ GetAuthorInfoById().handle_one_scholar_id() 回查 GS 主页
 │       ├─ 已存在（返回 True）跳过；新作者批量 upsert 到 scholar_author
 │       └─ 双兜底：全部无 GS 链接 / 全部查不到 → GetAuthorByTitle 按标题查
 └─ 每批记录 log_offset 断点
```

数据流向：年份页 → AJAX 批量 HTML → 论文记录（标题/作者/机构/引用/链接）→ PostgreSQL `article_paper_copilot` → 作者信息 → `scholar_author`。

## 核心代码片段

**AJAX 分批拉取直到结束标记**：

```python
while True:
    params = {"action": "load_paperlist", "batch": batch,
              "conf": ajaxmeta.get("conf", ""), "year": ajaxmeta.get("year", ""),
              "mode": ajaxmeta.get("mode", ""), "track": ajaxmeta.get("track", "")}
    # check_size/check_status_code=False：结束标记 "0" 只有 1 字节，不能被默认校验吞掉
    response = self.single_handler.fetch(ajax_url, params=params,
                                         check_size=False, check_status_code=False)
    if not response or response.status_code != 200:
        break
    if response.text.strip() == "0":
        break
    batch += 1
    res_text = (response.text.replace("\\/", "/").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&quot;", "'")
                .replace("&amp;", "&").replace('\\"', '"'))
    batch_papers = self.parse_paper_list_html(res_text, th_titles, initial_dict)
```

**表头动态分派解析器**（th 与 td 一一对应）：

```python
for th_title, td in zip(th_titles, td_tags):
    title = th_title.strip()
    if title == "Title":
        one_paper_info["article_title"] = td.select_one("a").get_text().strip()
        one_paper_info["article_url"] = td.select_one("a").get("href", "").strip()
        one_paper_info["social_links"] = {...}      # ul li a + title 属性
    elif title == "Authors":
        one_paper_info["authors"] = self.handle_td_author(td)   # data-gs/data-hp/data-dblp/data-or
    elif title == "Affiliation":
        one_paper_info["affiliation"] = self.handle_td_affiliation(td)
    elif "Country" in title:
        one_paper_info["country_of_affiliation"] = self.handle_td_country(td)
    elif "Citation" in title:
        one_paper_info["citation"] = self.handle_td_cite(td)    # data-gs/data-rating_avg/data-rating_str
    elif clean == "r#":
        continue                                # 排序号列无意义，跳过
```

**动态批量线程池**（先填满、边完成边补充，控制速率）：

```python
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    future_to_id = {}
    for _ in range(max_workers):                # 先填满线程池
        data = next(data_iter)
        future = executor.submit(self.handle_one_article,
                                 data["authors"], data["id"],
                                 data.get("article_title", ""))
        future_to_id[future] = data["id"]
    while future_to_id:
        for f in [f for f in future_to_id if f.done()]:
            article_id = future_to_id.pop(f)
            f.result()
            if not data_exhausted:              # 完成一个补充一个
                data = next(data_iter)
                future = executor.submit(self.handle_one_article,
                                         data["authors"], data["id"],
                                         data.get("article_title", ""))
                future_to_id[future] = data["id"]
```

# 示例项目

`examples/` 目录包含 28 个从实战中精选的爬虫示例，展示了 jimmySpider 框架在不同场景下的用法。

## 示例列表

| # | 示例 | 网站/数据源 | 数据库 | 关键特性 |
|---|------|------------|--------|----------|
| 1 | `hello_world` | Hacker News | MongoDB | 最简入门：`SingleRequestHandler` + `extractSoup` + `save_result` |
| 2 | `hello_mysql` | Hacker News | MySQL | 与 hello_world 相同逻辑，`db_type="mysql"` 一键切换后端 |
| 3 | `hello_postgresql` | Hacker News | PostgreSQL | 与 hello_world 相同逻辑，`db_type="postgresql"` 切换后端 |
| 4 | `eastmoney_report` | 东方财富研报 | MongoDB | GET/POST 多分类翻页、Redis 断点续爬、报告文件下载 |
| 5 | `state_council_policy` | 国务院政策 | MongoDB | list+detail 两阶段、ThreadPoolExecutor 并发详情、`HandleDatetime` |
| 6 | `moj_regulations` | 司法部法规 | MongoDB | JSON POST 签名请求、`safe_extract_json`、`rename_keys_inplace` |
| 7 | `medlive_guide` | 医脉通医学指南 | MongoDB | 多级分类树遍历、序列化分类断点（JSON）、动态 cookie |
| 8 | `cicc_report` | 中金研报 | MongoDB | 加速乐 CDN 挑战 JS 逆向（execjs）、报告 PDF 下载、批量 upsert |
| 9 | `unicamp_br` | 巴西大学机构库 | MongoDB | 按学科/年份/补充学科多策略并行采集 |
| 10 | `cuni_cz` | 查理大学 | MongoDB | list+detail、`ThreadRequestHandler` 多线程、批量 upsert 去重 |
| 11 | `escholarship_org` | eScholarship 论文库 | MongoDB | `CurlCffiAsyncRequestHandler`、AWS WAF token、DOI 去重 |
| 12 | `pubmed_ncbi` | PubMed | MongoDB | 按月分片突破 API 限制、多阶段流水线（列表→详情→JSONL→PDF） |
| 13 | `oatd` | OATD 学位论文 | MongoDB | `AsyncRequestHandler` 高并发、Cloudflare Turnstile CDP 绕过、Cookie 刷新 |
| 14 | `twse_taiwan` | 台湾证交所 MOPS | MongoDB | 重大讯息公告、按「民国年份×市场类别」分片、民国→公历转换 |
| 15 | `chinamoney` | 中国货币网 | MongoDB | 信用评级、POST 分页（年份×页码）、PDF 下载链接构造 |
| 16 | `naver_research` | Naver 研报（韩国） | MongoDB | 韩文页面解析、自定义 parser 路由表、韩文日期标准化 |
| 17 | `yaozh_pharma` | 药智网 | MongoDB | 登录会话依赖、data 属性分页、断点续爬 |
| 18 | `medsci` | 梅斯医学 | MongoDB | 分类接口动态下发、验证码风控感知、分类维度 JSON 断点 |
| 19 | `gspublishing` | 高盛研报 | MongoDB | POST JSON 复杂检索 payload、毫秒时间戳格式化、raw_data 保留 |
| 20 | `boc_fimarkets` | 中国银行金融市场 | MongoDB | list+detail、编码自适应（GBK/UTF-8）、附件拆分入库 |
| 21 | `arxiv_org` | arXiv | PostgreSQL | `CurlRequestHandler` TLS 伪装 + `ThreadRequestHandler` 并发、10 天窗口分片、PG 批量 upsert |
| 22 | `papercopilot` | Paper Copilot 会议论文 | PostgreSQL | AJAX 批量接口逆向、表头对齐解析、跨示例复用 google_scholar |
| 23 | `google_scholar` | Google Scholar | PostgreSQL | 4 文件流水线：按标题搜文章 → 作者主页 → 合作者扩散 |
| 24 | `tech_news_flash` | 科技快报网 | MySQL | GBK 编码页面、按文章编号抓取、MySQL 落库 |
| 25 | `tiantian_fund` | 天天基金 | MySQL | 排名分页 + 净值曲线 JS 解析、MySQL 双表 + CSV 兜底 |
| 26 | `tech_literature` | Frontiers 科技文献 | MySQL | 期刊 → 检索 API → 详情并发、结构化字段解析、期刊 JSON 缓存 |
| 27 | `robot_lab` | UC Berkeley BAIR 博客 | MySQL | 博客分页 + 详情并发、正文/作者/关键词提取 |
| 28 | `clash_proxy_pool` | Clash 节点池（docker-compose） | MongoDB | ClashManager 健康检测、按下载量/403 自动切换节点 |

## 运行示例

```bash
# 安装框架
pip install -e .

# 确保 MongoDB 和 Redis 在本地运行
# （MySQL / PostgreSQL 示例另需启动对应数据库）

# 运行示例（以 hello_world 为例）
cd examples/hello_world
python spider.py
```

## 按场景选示例

| 你想做什么 | 看哪个示例 |
|-----------|-----------|
| 刚接触框架，快速上手 | `hello_world` |
| 对比数据库后端（MySQL / PostgreSQL） | `hello_mysql`, `hello_postgresql` |
| 简单网站，翻页列表 | `eastmoney_report` |
| 政府网站，翻页列表 | `state_council_policy` |
| API JSON 采集 | `moj_regulations` |
| 分类树结构网站 | `medlive_guide` |
| 加速乐 CDN / JS 逆向 | `cicc_report` |
| 大学论文，多维度采集 | `unicamp_br` |
| 大学数据，list+detail | `cuni_cz` |
| AWS WAF / 高并发 | `escholarship_org` |
| 大型数据库，分片策略 | `pubmed_ncbi` |
| Cloudflare 保护 + 动态 Cookie | `oatd` |
| 金融数据（公告/评级/研报） | `twse_taiwan`, `chinamoney`, `gspublishing`, `boc_fimarkets`, `tiantian_fund` |
| 国际站点（韩文页面） | `naver_research` |
| 医药/医学数据库 | `yaozh_pharma`, `medsci` |
| 投行研报 JSON API | `gspublishing` |
| 银行官网 list+detail | `boc_fimarkets` |
| 学术预印本 | `arxiv_org` |
| 学术作者网络 / 跨示例流水线 | `papercopilot`, `google_scholar` |
| 新闻资讯站 | `tech_news_flash` |
| 科技期刊文献 | `tech_literature` |
| 实验室博客 | `robot_lab` |
| 代理池 / 节点切换 | `clash_proxy_pool` |

## 按请求处理器选示例

| 处理器 | 示例 |
|--------|------|
| `SingleRequestHandler` | hello_world, hello_mysql, hello_postgresql, eastmoney_report, state_council_policy, moj_regulations, medlive_guide, cicc_report, unicamp_br, twse_taiwan, chinamoney, naver_research, yaozh_pharma, medsci, gspublishing, boc_fimarkets, papercopilot, tech_news_flash, tech_literature, robot_lab, clash_proxy_pool |
| `AsyncRequestHandler` | oatd |
| `ThreadRequestHandler` | cuni_cz, pubmed_ncbi, arxiv_org |
| `CurlRequestHandler` | oatd（cookie_flush 场景）、arxiv_org, google_scholar |
| `CurlCffiThreadRequestHandler` | — |
| `CurlCffiAsyncRequestHandler` | escholarship_org |

## 按数据库后端选示例

| 数据库 | 示例 |
|--------|------|
| MongoDB（默认） | hello_world, eastmoney_report, state_council_policy, moj_regulations, medlive_guide, cicc_report, unicamp_br, cuni_cz, escholarship_org, pubmed_ncbi, oatd, twse_taiwan, chinamoney, naver_research, yaozh_pharma, medsci, gspublishing, boc_fimarkets, clash_proxy_pool |
| MySQL | hello_mysql, tech_news_flash, tiantian_fund, tech_literature, robot_lab |
| PostgreSQL | hello_postgresql, arxiv_org, papercopilot, google_scholar |

> 通过构造参数 `db_type` 或 `jimmyspider.yaml` 中的 `db_type` 配置切换后端（默认 mongodb），表结构由对应 Handler 自动创建。

## 编写你自己的爬虫

1. 从最接近你场景的示例入手
2. 复制示例到新目录
3. 修改 URL、解析逻辑、字段映射
4. 运行测试

# 示例项目

`examples/` 目录包含 13 个从实战中精选的爬虫示例，展示了 jimmySpider 框架在不同场景下的用法。

## 示例列表

### 1. hello_world — 入门示例

**文件**: `spider.py`  
**展示特性**: 
- 最简完整爬虫（约 50 行，含注释），新用户从这里开始
- `JimmySpider` 基类自动装配（日志 / MongoDB / HTML 解析 / 请求处理器）
- `SingleRequestHandler` 同步请求
- `extractSoup` HTML 解析 + `save_result()` 入库

**适用场景**: 框架入门、理解「继承基类 → 发请求 → 解析 → 入库」最小闭环

### 2. eastmoney_report — 东方财富研报

**文件**: `spider.py`  
**展示特性**: 
- `SingleRequestHandler` GET/POST 请求
- 多分类翻页
- Redis 断点续爬（页码缓存 + 错误页集合）
- 报告文件下载

**适用场景**: 金融数据、研报采集、分页 API

### 3. state_council_policy — 国务院政策

**文件**: `spider.py`  
**展示特性**:
- list+detail 两阶段爬取模式
- ThreadPoolExecutor 并发抓取详情页
- BeautifulSoup HTML 字段解析
- `HandleDatetime` 日期标准化

**适用场景**: 政府网站、政策法规、新闻列表

### 4. moj_regulations — 司法部法规

**文件**: `spider.py`  
**展示特性**:
- JSON POST 请求（带签名 header）
- `safe_extract_json()` 安全提取嵌套 JSON
- `rename_keys_inplace()` 字段映射
- 分批入库 + 断点页码

**适用场景**: API 接口采集、JSON 数据解析

### 5. medlive_guide — 医学指南

**文件**: `spider.py`, `spider_type.py`  
**展示特性**:
- 多级分类树遍历
- 序列化的分类断点缓存（JSON）
- 单分类完整爬取 vs 全分类遍历两种模式
- 医学网站反爬对抗（动态 cookie）

**适用场景**: 分类树结构网站、医学/学术内容

### 6. cicc_report — 券商报告

**文件**: `spider.py`  
**展示特性**:
- 加速乐 CDN Cookie 挑战求解（execjs + JS 逆向）
- JS 加密上下文集成（`_load_cookie_js_ctx`）
- 报告 PDF 下载 + JSON 元数据提取
- 多页翻页 + MongoDB 批量 upsert

**适用场景**: 加速乐 CDN 保护网站、JS 逆向 Cookie 求解

### 7. unicamp_br — 巴西大学机构库

**文件**: `spider_list_by_area.py`, `spider_list_by_year.py`, `spider_supplement_by_area.py`  
**展示特性**:
- 多策略并行采集（按学科/按年份/按补充学科）
- JSON 层次结构数据加载
- 大学机构库论文元数据采集
- 进度持久化 + 错误重试

**适用场景**: 大学机构库、学术论文、多维度采集

### 8. cuni_cz — 查理大学

**文件**: `spider_list.py`, `spider_detail.py`  
**展示特性**:
- list+detail 完整模式
- `ThreadRequestHandler` 多线程请求
- 学院列表 JSON 数据驱动
- MongoDB 批量 upsert + 去重

**适用场景**: 大学教职工/论文数据库、跨学院采集

### 9. escholarship_org — 学术论文库

**文件**: `spider_list.py`, `spider_detail.py`  
**展示特性**:
- `CurlCffiAsyncRequestHandler` curl_cffi 异步
- AWS WAF token 处理
- 论文元数据 + 附件下载
- DOI 去重 + 断点恢复

**适用场景**: AWS WAF 保护站点、高并发学术数据采集

### 10. pubmed_ncbi — PubMed

**文件**: `spider_list.py`, `spider_detail.py`, `spider_50_to_jsonl.py`, `springer_down_txt_xm.py`  
**展示特性**:
- 日期分段策略（按月拆分，突破 API 限制）
- ThreadPoolExecutor 多线程（10 workers）
- 多阶段流水线（列表 → 详情 → JSONL → PDF 下载）
- PubMed API + Springer 全文下载

**适用场景**: 大型学术数据库、API 分片策略、多阶段数据流水线

### 11. oatd — OATD 学位论文

**文件**: `spider.py`, `spider_list.py`, `cookie_flush_playwright_cdp.py`  
**展示特性**:
- `AsyncRequestHandler` aiohttp 异步高并发
- Cloudflare Turnstile 验证绕过（Playwright CDP）
- Cookie 刷新 + 代理轮换
- `asyncio.Semaphore` 并发控制

**适用场景**: Cloudflare 保护的学术网站、需动态 cookie 刷新的场景

### 12. twse_taiwan — 台湾证交所 MOPS

**文件**: `spider.py`  
**展示特性**:
- 金融数据抓取：上市/上柜/兴柜/公开发行的重大讯息公告
- POST JSON 接口按「民国年份 × 市场类别」分片
- 动态表格解析（`header + titles + data`）+ 民国 → 公历日期转换
- `ThreadPoolExecutor` 5 线程并发抓取详情 + 正文提取 + HTML 快照
- Redis 断点续爬（分片完成集合 / 公告类型去重 / 错误分片与详情重试）

**适用场景**: 金融数据、动态表格 JSON API、按片区分段采集

### 13. chinamoney — 中国货币网信用评级

**文件**: `spider.py`  
**展示特性**:
- POST form API 分页（年份 × 页码，每页 30 条）
- 字段标准化 + PDF 下载链接构造（`file_url` + `file_type`）
- Redis 断点续爬（年份+页码断点 + 失败页重试）
- 页间限速（`time.sleep(1)`）

**适用场景**: 金融数据、POST API 分页、断点精确恢复

## 运行示例

```bash
# 安装框架
pip install jimmyspider

# 确保 MongoDB 和 Redis 在本地运行

# 运行示例（以 hello_world 为例）
cd examples/hello_world
python spider.py
```

## 按场景选示例

| 你想做什么 | 看哪个示例 |
|-----------|-----------|
| 刚接触框架，快速上手 | `hello_world` |
| 简单网站，翻页列表 | `eastmoney_report` |
| 政府网站，翻页列表 | `state_council_policy` |
| API JSON 采集 | `moj_regulations` |
| 分类树结构网站 | `medlive_guide` |
| 加速乐 CDN / JS 逆向 | `cicc_report` |
| 大学论文，多维度采集 | `unicamp_br` |
| 大学数据，list+detail | `cuni_cz` |
| AWS WAF / 高并发 | `escholarship_org` |
| 大型数据库，分片策略 | `pubmed_ncbi` |
| 金融数据（公告/评级/研报） | `twse_taiwan`, `chinamoney` |

## 按请求处理器选示例

| 处理器 | 示例 |
|--------|------|
| `SingleRequestHandler` | hello_world, eastmoney_report, state_council_policy, moj_regulations, twse_taiwan, chinamoney |
| `AsyncRequestHandler` | oatd |
| `ThreadRequestHandler` | cuni_cz, pubmed_ncbi |
| `CurlRequestHandler` | oatd（cookie_flush 场景下可用于 TLS 伪装） |
| `CurlCffiThreadRequestHandler` | — |
| `CurlCffiAsyncRequestHandler` | escholarship_org |

## 编写你自己的爬虫

1. 从最接近你场景的示例入手
2. 复制示例到新目录
3. 修改 URL、解析逻辑、字段映射
4. 运行测试

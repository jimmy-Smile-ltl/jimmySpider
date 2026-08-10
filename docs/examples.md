# 示例项目

`examples/` 目录包含 9 个从实战中精选的爬虫示例，展示了 jimmySpider 框架在不同场景下的用法。

## 示例列表

### 1. eastmoney_report — 东方财富研报

**文件**: `spider.py`  
**展示特性**: 
- `SingleRequestHandler` GET/POST 请求
- 多分类翻页
- Redis 断点续爬（页码缓存 + 错误页集合）
- 报告文件下载

**适用场景**: 金融数据、研报采集、分页 API

### 2. state_council_policy — 国务院政策

**文件**: `spider.py`  
**展示特性**:
- list+detail 两阶段爬取模式
- ThreadPoolExecutor 并发抓取详情页
- BeautifulSoup HTML 字段解析
- `HandleDatetime` 日期标准化

**适用场景**: 政府网站、政策法规、新闻列表

### 3. moj_regulations — 司法部法规

**文件**: `spider.py`  
**展示特性**:
- JSON POST 请求（带签名 header）
- `safe_extract_json()` 安全提取嵌套 JSON
- `rename_keys_inplace()` 字段映射
- 分批入库 + 断点页码

**适用场景**: API 接口采集、JSON 数据解析

### 4. medlive_guide — 医学指南

**文件**: `spider.py`, `spider_type.py`  
**展示特性**:
- 多级分类树遍历
- 序列化的分类断点缓存（JSON）
- 单分类完整爬取 vs 全分类遍历两种模式
- 医学网站反爬对抗（动态 cookie）

**适用场景**: 分类树结构网站、医学/学术内容

### 5. cicc_report — 券商报告

**文件**: `spider.py`  
**展示特性**:
- `CurlRequestHandler` TLS 指纹伪装（chrome120）
- 加速乐 CDN Cookie 挑战求解
- JS 加密上下文集成（`_load_cookie_js_ctx`）
- 报告 PDF 下载 + JSON 元数据提取

**适用场景**: Cloudflare/加速乐保护网站、需要 TLS 指纹伪装的场景

### 6. unicamp_br — 巴西大学机构库

**文件**: `spider_list_by_area.py`, `spider_list_by_year.py`, `spider_supplement_by_area.py`  
**展示特性**:
- 多策略并行采集（按学科/按年份/按补充学科）
- JSON 层次结构数据加载
- 大学机构库论文元数据采集
- 进度持久化 + 错误重试

**适用场景**: 大学机构库、学术论文、多维度采集

### 7. cuni_cz — 查理大学

**文件**: `spider_list.py`, `spider_detail.py`  
**展示特性**:
- list+detail 完整模式
- `ThreadRequestHandler` 多线程请求
- 学院列表 JSON 数据驱动
- MongoDB 批量 upsert + 去重

**适用场景**: 大学教职工/论文数据库、跨学院采集

### 8. escholarship_org — 学术论文库

**文件**: `spider_list.py`, `spider_detail.py`  
**展示特性**:
- `CurlCffiAsyncRequestHandler` curl_cffi 异步
- AWS WAF token 处理
- 论文元数据 + 附件下载
- DOI 去重 + 断点恢复

**适用场景**: AWS WAF 保护站点、高并发学术数据采集

### 9. pubmed_ncbi — PubMed

**文件**: `spider_list.py`, `spider_detail.py`, `spider_50_to_jsonl.py`, `springer_down_txt_xm.py`  
**展示特性**:
- 日期分段策略（按月拆分，突破 API 限制）
- ThreadPoolExecutor 多线程（10 workers）
- 多阶段流水线（列表 → 详情 → JSONL → PDF 下载）
- PubMed API + Springer 全文下载

**适用场景**: 大型学术数据库、API 分片策略、多阶段数据流水线

## 运行示例

```bash
# 安装框架
pip install jimmyspider

# 确保 MongoDB 和 Redis 在本地运行

# 运行示例（以 state_council_policy 为例）
cd examples/state_council_policy
python spider.py
```

## 按场景选示例

| 你想做什么 | 看哪个示例 |
|-----------|-----------|
| 简单网站，快速上手 | `eastmoney_report` |
| 政府网站，翻页列表 | `state_council_policy` |
| API JSON 采集 | `moj_regulations` |
| 分类树结构网站 | `medlive_guide` |
| Cloudflare / TLS 检测 | `cicc_report` |
| 大学论文，多维度采集 | `unicamp_br` |
| 大学数据，list+detail | `cuni_cz` |
| AWS WAF / 高并发 | `escholarship_org` |
| 大型数据库，分片策略 | `pubmed_ncbi` |

## 按请求处理器选示例

| 处理器 | 示例 |
|--------|------|
| `SingleRequestHandler` | eastmoney_report, state_council_policy, moj_regulations |
| `AsyncRequestHandler` | — |
| `ThreadRequestHandler` | cuni_cz, pubmed_ncbi |
| `CurlRequestHandler` | cicc_report |
| `CurlCffiThreadRequestHandler` | — |
| `CurlCffiAsyncRequestHandler` | escholarship_org |

## 编写你自己的爬虫

1. 从最接近你场景的示例入手
2. 复制示例到新目录
3. 修改 URL、解析逻辑、字段映射
4. 运行测试

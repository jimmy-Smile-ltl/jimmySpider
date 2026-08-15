# parser_demo —— 智能解析引擎演示（选择器级联）

> 演示 `jimmyspider.parser`：对 4 种完全不同结构的页面（新闻 / 博客 / 商品页 / 裸结构页），用 `TitleExtractor`、`ContentExtractor`、`SelectorCascade` 自动提取标题、正文、日期、价格、SKU，输出每个字段的 `value / method / confidence / selector`。**完全离线运行**，内置 mock HTML 与 mock LLM，不消耗任何真实 token。

## 站点

- 演示对象：4 种内置 mock 页面（无需联网）：
  - A. 新闻文章（日报站）`daily.example.com` — h1 + og:title + JSON-LD 齐全
  - B. 博客文章 `blog.example.com` — 标题只存在于 JSON-LD（无 h1 无标题 class）
  - C. 商品详情页 `shop.example.com/product/x1-pro` — og:title + `.price` + 隐藏 SKU
  - D. 裸结构页面 `legacy.example.com/page/8848` — 无 class、无 id、无 meta（考验 DOM 密度分析）
- 真实接入时把 `SAMPLES` 换成任意目标站 HTML 即可

## 展示特性

- **TitleExtractor 中文标题专项提取**：h1 → 语义 class → meta/JSON-LD → DOM → LLM 的策略链
- **ContentExtractor 正文提取**：语义选择器 → 文本密度 → 递归 → body 兜底
- **SelectorCascade 五层级联引擎**（本次演示覆盖全部 5 层）：

| 层级 | 策略 | 成本 | 置信度 | 本次演示命中 |
|------|------|------|--------|--------------|
| L0 | 已知定位器（known_selectors，缓存/配置复用） | 0ms / 0 token | 1.00 | 新闻页 `{"title": "h1", "content": ".article-content"}` |
| L1 | 语义选择器（h1 / .article-title / .price） | ~1ms | 0.85~0.99 | 新闻页 h1、商品页 .price |
| L2 | JSON-LD / OpenGraph / Meta | ~2ms | 0.90 | 博客页 JSON-LD、商品页 og:title |
| L3 | DOM 密度分析（阅读器算法） | ~5ms | 0.75 | 博客/商品/裸结构页正文 |
| L4 | LLM 语义兜底（mock 占位） | ~2s / 500+ token | 0.95 | 商品页 sku、裸结构页标题 |

- **结构化结果**：每个字段返回 `ExtractionResult`（value / method / confidence / selector_used / latency_ms / is_valid），candidates 保留全部候选值可人工复核
- **可复用定位器输出**：`PageResult.selectors` 即下次抓取的 known_selectors，同站点第二次抓取走 L0 零成本命中
- **成本估算演示**：4 页 9 字段级联仅 ~1000 tokens（2 字段落 LLM），全量 LLM ~4500 tokens，差约 4 倍；规模化后抓 10 万页/天，LLM 调用量从 90 万次降到接近零
- **字段顺序敏感**：正文的密度分析会就地剥离 script/style 等标签（共享 soup），依赖 JSON-LD 的字段（日期等）要排在 content 之前
- **运行输出自解释**：每个字段标注命中层级，如 `sku: llm | llm | conf=0.95`、`title: semantic | h1 | conf=0.99`，整页输出 `total_latency_ms / extractors_used / tokens_used`

**什么时候启用 `llm_call`**：

- 不建议默认开 —— 新站点先用纯级联跑一天，统计 `method=failed` 的字段
- 只有少量「顽固字段」（商品 SKU、结构化表格等）失败时，再为其单独启用 LLM 兜底
- `llm_call` 需返回 `None` 表示「该字段不存在」，避免 LLM 无中生有
- 级联成功后会记录定位器（`PageResult.selectors`），下次直接走 L0 零成本命中

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 演示脚本（单文件）：3 组演示（专项提取 / 五层级联 / 成本对比）+ 4 种 mock 页面 + mock LLM |

## 运行方式

```bash
cd examples/parser_demo
python spider.py        # 零依赖：bs4 + lxml + jimmyspider（框架依赖已含）
```

## 前置条件

- 完全离线：不联网、不消耗 token、不依赖 Redis / MongoDB
- 需安装 `jimmyspider` 框架（含 parser 模块）+ `beautifulsoup4` / `lxml`
- Windows 控制台自动切换 UTF-8 输出（`sys.stdout.reconfigure`）

## 爬虫架构

```
main()
 ├─ demo_extractors()   # TitleExtractor + ContentExtractor 对 4 样本逐页提取
 ├─ demo_cascade()      # SelectorCascade 五层级联 5 组场景
 │   ├─ L0: known_selectors={"title": "h1", "content": ".article-content"}
 │   ├─ L1: 无已知定位器，语义选择器命中
 │   ├─ L2: 博客页标题只存在 JSON-LD
 │   ├─ L4: 商品页 sku 全策略失败 → llm_call=mock_llm_call 兜底
 │   ├─ L3: 裸结构页正文由文本密度定位
 │   └─ 输出 result.selectors 供下次复用（L0 缓存）
 └─ demo_cost()         # 级联 vs 全量 LLM token 对比
```

每个字段的级联决策：从 L0 开始按成本升序尝试，`confidence >= 阈值` 即返回；全部失败才落 L4 LLM（`llm_call` 需返回 `None` 表示「该字段不存在」，避免 LLM 无中生有）。置信度阈值可调：`SelectorCascade(field_thresholds={"title": 0.9})`。

## 核心代码片段

**级联提取入口**（schema 定义字段，llm_call 可选）：

```python
cascade = SelectorCascade()
result = cascade.extract(
    s["html"], url=s["url"],
    schema={"fields": [
        {"name": "title", "type": "text"},
        {"name": "publish_date", "type": "text"},   # 排在 content 之前！
        {"name": "content", "type": "text"},        # 密度分析会剥离 script 标签
    ]},
    llm_call=mock_llm_call,     # 真实项目换成自己的 LLM 调用即可
)
print(result.summary())
print(result.selectors)         # 可复用的定位器字典（下次作为 known_selectors）
```

**L0 已知定位器直出**（首次抓取后缓存复用，0 token 且置信度 1.00）：

```python
cascade = SelectorCascade(
    known_selectors={"title": "h1", "content": ".article-content"}
)
result = cascade.extract(SAMPLE_NEWS["html"], url=SAMPLE_NEWS["url"],
                         schema=schema_common, llm_call=mock_llm_call)
print(result.selectors)   # 输出可复用定位器，下次直接作为 known_selectors
```

**mock LLM 兜底**（真实项目接 OpenAI/DeepSeek 同签名）：

```python
def mock_llm_call(html: str, field_name: str, field_def: dict) -> str:
    placeholders = {"title": "《未知名文章：正文首句提炼的标题》",
                    "sku": "SKU-X1-PRO-2026-旗舰款"}
    return placeholders.get(field_name, f"【LLM 占位】{field_name} = 未在页面中定位到对应内容")
```

**成本对比统计**（按字段 method 计数落 L4 的数量）：

```python
total_tokens += r.tokens_used
llm_fields += sum(1 for f in r.fields.values() if f.method == "llm")
print(f"级联引擎: {field_count} 个字段 → 仅 {total_tokens} tokens"
      f"（{llm_fields} 个字段落到 Level 4 LLM 兜底）")
print(f"全量 LLM : 约 {field_count * 500} tokens")
```

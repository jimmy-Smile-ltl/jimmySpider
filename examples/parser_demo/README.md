# parser_demo —— 智能解析演示（选择器级联引擎）

> 演示 `jimmyspider.parser`：对 4 种完全不同结构的页面（新闻 / 博客 / 商品页 /
> 裸结构页），用 `TitleExtractor`、`ContentExtractor`、`SelectorCascade` 自动提取
> 标题、正文、日期、价格、SKU，输出每个字段的 `value / method / confidence / selector`。
> **完全离线运行**，内置 mock HTML 与 mock LLM，不消耗任何真实 token。

```bash
python spider.py        # 零依赖：bs4 + lxml + jimmyspider（框架依赖已含）
```

## 五层级联（本次演示覆盖全部 5 层）

| 层级 | 策略 | 成本 | 置信度 | 本次演示命中 |
|------|------|------|--------|--------------|
| L0 | 已知定位器（缓存/配置文件复用） | 0ms / 0 token | 1.00 | 新闻页 `known_selectors={"title":"h1"}` |
| L1 | 语义选择器（h1 / .article-title / .price） | ~1ms | 0.85~0.99 | 新闻页 h1、商品页 .price |
| L2 | JSON-LD / OpenGraph / Meta | ~2ms | 0.90 | 博客页 JSON-LD、商品页 og:title |
| L3 | DOM 密度分析（阅读器算法） | ~5ms | 0.75 | 博客/商品/裸结构页正文 |
| L4 | LLM 语义兜底（mock 占位） | ~2s / 500+ token | 0.95 | 商品页 sku、裸结构页标题 |

运行输出中每个字段都标注了命中层级，例如：
`sku: llm | llm | conf=0.95`、`title: semantic | h1 | conf=0.99`。

## 成本分析（LLM vs 级联）

演示脚本最后会打印实测成本：4 个页面 9 个字段，级联仅消耗 **1000 tokens**
（2 个字段落到 LLM 兜底），全量 LLM 约 **4500 tokens**，差约 **4 倍**。
规模化后的差距更悬殊：抓 10 万页/天，级联让 LLM 调用量从 90 万次降到几乎为零
（同站点第二次抓取即可复用 L0 定位器，`result.selectors` 输出即复用字典）。

**什么时候启用 `llm_call`**：

- 不建议默认开 —— 新站点先用纯级联跑一天，统计 `method=failed` 的字段
- 只有少量「顽固字段」（商品 SKU、结构化表格等）失败时，再为其单独启用 LLM 兜底
- `llm_call` 需返回 `None` 表示「该字段不存在」，避免 LLM 无中生有
- 级联成功后会记录定位器（`PageResult.selectors`），下次直接走 L0 零成本命中

## 工程要点

- **字段顺序有讲究**：正文的密度分析会就地剥离 script/style 等标签（共享 soup），
  依赖 JSON-LD 的字段（日期等）应排在 content 之前
- `ExtractionResult.candidates` 保留全部候选值，可人工复核
- 置信度阈值可调：`SelectorCascade(field_thresholds={"title": 0.9})`
- 接入真实 LLM 只需一行：`llm_call=my_async_llm_call`（框架内部按同步调用约定）

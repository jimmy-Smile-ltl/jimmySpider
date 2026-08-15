# 网页智能解析模块 — 选择器级联提取引擎（迁移至 jimmySpider）

> 本模块由 `spider research/爬虫架构/smart_parser/` 迁移而来，现位于
> `jimmyspider/parser/`，通过 `from jimmyspider.parser import SelectorCascade` 使用。

## 核心洞察（来自 1069 个真实日报站点数据）

```
title_selector 分布:          content_selector 分布:
  h1            96.2%           #ozoom         95.8%
  h2             0.4%           .flow           0.1%
  h3             0.1%           其他             4.1%
  其他            3.3%

结论: 日报/新闻类网站的标题和正文选择器极其集中。
     → 语义选择器 (h1, #ozoom) 可覆盖 96%+
     → LLM 仅需在首次遇到新站点时调用一次 (<4% 的页面)
     → selector 自动缓存 → 后续 0 token 成本
```

---

## 一、优化后的五层架构（Token 成本优先）

```
┌──────────────────────────────────────────────────────────────┐
│                  SmartParser Engine (v2)                      │
│                                                               │
│  Layer 0: sites.yaml 已知规则  → 0ms, 0 token, 100% 准确    │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ 从历史积累的 selector 库直接匹配 (1069条规则)          │     │
│  │ 命中率: 100% (已知站点) / 0% (新站点)                 │     │
│  └─────────────────────────────────────────────────────┘     │
│         ↓ 命中率 < 100% (新站点)                              │
│                                                               │
│  Layer 1: 语义选择器 (CSS)   → ~0.5ms, 0 token, ~96% 准确   │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ h1 → .title → [class*=headline] → h2 → h3           │     │
│  │ #ozoom → article → .content → 文本密度算法           │     │
│  │ 命中率: ~98% (新闻) / ~80% (通用)                     │     │
│  └─────────────────────────────────────────────────────┘     │
│         ↓ 命中率 < 80%                                       │
│                                                               │
│  Layer 2: 结构化数据   → ~1ms, 0 token, ~90% 准确           │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ JSON-LD (Schema.org) → OpenGraph meta → <title> tag  │     │
│  │ meta[name="author"] → meta[article:published_time]   │     │
│  └─────────────────────────────────────────────────────┘     │
│         ↓ 命中率 < 90%                                       │
│                                                               │
│  Layer 3: DOM 特征分析 → ~5ms, 0 token, ~75% 准确           │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ 文本密度 (Readability) → 中文标题特征评分              │     │
│  │ 字体大小/标签层级/文本位置                             │     │
│  └─────────────────────────────────────────────────────┘     │
│         ↓ 命中率 < 75% (罕见)                                │
│                                                               │
│  Layer 4: LLM 兜底       → ~2s, 300 tokens, 95%+ 准确       │
│  ┌─────────────────────────────────────────────────────┐     │
│  │ HTML→Markdown 压缩 → Schema Prompt → JSON 输出        │     │
│  │ 成功后自动回写 selector 到缓存                         │     │
│  └─────────────────────────────────────────────────────┘     │
│                                                               │
│  关键优化: LLM 调用后 → selector 自动缓存 → 后续 0 cost      │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、Selector 级联引擎（核心实现）

### 2.1 文件结构

```
jimmyspider/parser/
├── README.md                        # 本文件
├── __init__.py                      # 公开 API: SelectorCascade / TitleExtractor /
│                                    #   ContentExtractor / ExtractionResult / PageResult
├── cascade.py                       # SelectorCascade 核心引擎
├── title_extractor.py               # TitleExtractor (中文标题专项优化)
├── content_extractor.py             # ContentExtractor (正文提取)
└── tests/
    └── test_newspaper_titles.py     # 1069 日报测试 + 统计 (参考测试)
```

### 2.2 SelectorCascade 引擎

```python
from jimmyspider.parser import SelectorCascade

# 从 sites.yaml 加载已知规则
cascade = SelectorCascade(
    known_selectors={"title": "h1", "content": "#ozoom"}
)

# 提取 — 自动按成本最低路径执行
result = cascade.extract(html, url="https://...", schema={
    "fields": [
        {"name": "title", "type": "text"},
        {"name": "content", "type": "html"},
    ]
})

# 输出
print(result.selectors)  # → {"title": "h1", "content": "#ozoom"}
print(result.tokens_used)  # → 0  (未使用 LLM)
```

### 2.3 单字段提取流程

```python
def _extract_field(field_name, threshold=0.80):
    # Level 0: 已知定位器（成本=0）
    if field_name in known_selectors:
        value = apply_selector(known_selectors[field_name])
        if value: return (value, "selector", 1.0)

    # Level 1: 语义选择器（成本=0, 覆盖96%+）
    for sel in get_semantic_selectors(field_name):  # h1, .title, h2...
        value = apply_selector(sel)
        confidence = score_value(value)
        if confidence >= threshold:
            cache_selector(domain, field_name, sel)  # 自动缓存
            return (value, "semantic", confidence)

    # Level 2: 结构化数据（成本=0）
    value = extract_from_jsonld_or_meta(field_name)
    if value: return (value, "meta", 0.88)

    # Level 3: DOM 特征（成本=0）
    value = extract_by_dom_features(field_name)
    if value: return (value, "dom", 0.70)

    # Level 4: LLM（成本最高, 调用一次后缓存）
    value = llm_extract(html, field_name)
    if value:
        cache_selector(domain, field_name, infer_selector(html, value))
        return (value, "llm", 0.95)

    return (None, "failed", 0.0)
```

---

## 三、中文标题专项优化

### 3.1 验证规则

从 1069 个日报站点总结的标题验证规则：

```python
def is_valid_chinese_title(text: str) -> bool:
    # 1. 长度: 3-200 字符
    if len(text.strip()) < 3 or len(text.strip()) > 200:
        return False
    # 2. 至少含 2 个中文字符
    if len(re.findall(r'[一-鿿]', text)) < 2:
        return False
    # 3. 排除导航文本
    noise = {"首页", "上一页", "下一页", "登录", "注册", "返回"}
    if text.strip() in noise:
        return False
    # 4. 中文占比 > 30%
    chinese_ratio = len(re.findall(r'[一-鿿]', text)) / len(text)
    if chinese_ratio < 0.3:
        return False
    return True
```

### 3.2 评分维度

| 维度 | 权重 | 说明 |
|------|:----:|------|
| 长度 10-80 字 | +3 | 日报标题典型长度 |
| 中文字符 >50% | +2~3 | 中文越多越好 |
| 标签是 h1 | +3 | 最强语义信号 |
| 标签是 h2 | +2 | |
| 非导航文本 | +1 | 排除"首页""登录"等 |
| 标点占比 <30% | +1 | 排除装饰性文本 |

### 3.3 置信度阈值策略

| 页面类型 | title 阈值 | content 阈值 | 说明 |
|---------|:---------:|:------------:|------|
| 日报/新闻 | 0.95 | 0.70 | 高要求标题，正文容错 |
| 商品详情 | 0.80 | 0.65 | 标题可能包含品牌名 |
| 论坛帖子 | 0.75 | 0.60 | 标题格式多样 |
| 通用页面 | 0.85 | 0.70 | 默认 |

---

## 四、Token 成本优化分析

### 4.1 实际数据测算（1069 日报站点）

```
场景: 采集 1000 个日报站点，每个站点 100 篇文章 = 100,000 页面

┌─────────────────────────────────────────────────────────────┐
│  策略                         Token 消耗      成本          │
├─────────────────────────────────────────────────────────────┤
│  A: 全部 LLM                  30,000,000    $24.00         │
│     (每页 300 tokens)                                      │
│                                                             │
│  B: 已知 selector + 语义      0             $0.00          │
│     (sites.yaml h1 = 96.2%)                                │
│                                                             │
│  C: B + 新站点 LLM 兜底       12,000        $0.01          │
│     (约 40 个新站点, 每站1次LLM, 自动缓存)                 │
│                                                             │
│  节省: 99.96% ($24.00 → $0.01)                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 缓存策略

```python
# 三级缓存
class SelectorCache:
    # Level 1: 进程内存 (最快, 同进程复用)
    _memory: dict[str, dict] = {}   # {domain: {"title": "h1", ...}}

    # Level 2: Redis (进程间共享, 容器重启保留)
    # key: selector_cache:{domain}
    # value: {"title":"h1","content":"#ozoom","updated":"2026-06-29"}

    # Level 3: sites.yaml (永久存储, Git 版本控制)
    # 人工审核后写入
```

---

## 五、与传统方式对比（优化后）

| 维度 | 传统 XPath/CSS | v1 原设计 | v2 优化后 |
|------|:---:|:---:|:---:|
| 新站点成本 | 人工 30min | Schema 2min | Schema 2min |
| 网站改版 | 全部失效 | 自动降级 | 自动降级 + 自动发现新 selector |
| Token 成本 | 0 | 中（LLM调用较多） | **极低** (96%+ 情况为0) |
| 提取精度 | 99%+ | 90-99% | 96-99% |
| 速度 | ~1ms | 1ms~2s | **~1ms** (96%+ 情况) |
| 维护成本 | 高 | 低 | **极低** (selector自动缓存) |
| 日报覆盖率 | 100% (已知) | 85% (泛化) | **100%** (已知) + **98%** (新) |

---

## 六、集成到爬虫管线

```python
from jimmyspider import JimmySpider
from jimmyspider.parser import SelectorCascade


class SmartSpider(JimmySpider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 加载已知规则 (可从 sites.yaml / Redis 缓存读取)
        known = self._load_known_selectors()
        self.parser = SelectorCascade(known_selectors=known)

    def parse_detail(self, response):
        result = self.parser.extract(
            response.text, url=response.url,
            schema=self.schema,
            llm_call=self._llm_fallback  # 仅必要时调用
        )

        # 新发现的 selector → 保存
        if result.selectors:
            self._save_selectors(response.url, result.selectors)

        return result.fields

    def _llm_fallback(self, html, field_name, field_def):
        """LLM 兜底 — 仅在语义选择器全部失败时调用"""
        # 仅对未命中字段调用 LLM
        ...
```

---

## 七、数据依赖

### 运行期（模块本身）

- 仅依赖第三方库: `beautifulsoup4` (bs4) + `lxml`（使用 XPath 选择器时按需导入）。
- **不加载任何外部数据文件**。`cascade.py` / `title_extractor.py` 中提到的 `sites.yaml`
  只是 Layer 0 的数据来源概念 — 调用方（爬虫）读取站点规则后通过
  `SelectorCascade(known_selectors=...)` / `extract(known_selector=...)` 传入即可。
  站点规则数据文件位于原始研究仓库:
  `C:\Users\JimmySmile\Documents\Python code\Spider\spider research\agents\日报\knowledge\sites.yaml`
  （1069 条日报站点规则，git 版本控制，人工审核维护）。

### 测试期（tests/）

- `tests/test_newspaper_titles.py` 依赖:
  - 上述 `sites.yaml`（测试文件内为**硬编码绝对路径**，若数据文件位置变化需同步修改）
  - `pyyaml`（读取 sites.yaml）、`requests`（抓取真实页面，测试 1/2 需要网络，默认跳过）
  - 运行方式（相对导入，需以模块方式运行）:
    ```bash
    cd 开源 整理/jimmySpider
    python -m jimmyspider.parser.tests.test_newspaper_titles
    ```

---

## 八、文件清单

```
jimmyspider/parser/
├── README.md                          # 本文件 (迁移版设计文档)
├── __init__.py                        # 公开 API 再导出
├── cascade.py                         # SelectorCascade 核心 (Level 0→4 自动降级 + selector缓存)
├── title_extractor.py                 # 中文标题专项 (评分体系 + 语义选择器序列 + 验证规则)
├── content_extractor.py               # 正文提取 (文本密度 + 递归提取 + #ozoom特殊处理)
└── tests/test_newspaper_titles.py     # 1069日报数据测试 (选择器分布统计 / Token 节省测算)
```

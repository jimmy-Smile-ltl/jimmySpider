"""
网页智能解析模块 — 选择器级联提取引擎

从 `spider research/爬虫架构/smart_parser/` 迁移而来（面向日报/新闻站点的五层级联提取）:

  - SelectorCascade:   核心级联引擎（已知定位器 → 语义选择器 → JSON-LD/Meta → DOM 分析 → LLM 兜底）
  - TitleExtractor:    中文标题专项提取器（基于 1069 个真实日报站点统计优化）
  - ContentExtractor:  正文提取器（语义选择器 + 文本密度算法 + 递归提取 + 云展网 #ozoom 特殊处理）
  - ExtractionResult:  单字段提取结果（value / selector_used / method / confidence / latency_ms）
  - PageResult:        整页提取结果（fields / selectors / tokens_used / summary）

用法:
    from jimmyspider.parser import SelectorCascade

    cascade = SelectorCascade(known_selectors={"title": "h1"})
    result = cascade.extract(html, url="https://...", schema={
        "fields": [{"name": "title", "type": "text"}, {"name": "content", "type": "text"}]
    })
    print(result.selectors)     # → {"title": "h1"}
    print(result.tokens_used)   # → 0 (未使用 LLM)

注意: 模块仅依赖 bs4 + lxml（可选）。sites.yaml 等外部数据文件不在此模块内加载，
由调用方读取后通过 known_selectors 参数传入（详见 README.md 的"数据依赖"章节）。
"""

from .cascade import SelectorCascade, ExtractionResult, PageResult
from .title_extractor import TitleExtractor
from .content_extractor import ContentExtractor

__all__ = [
    "SelectorCascade",
    "TitleExtractor",
    "ContentExtractor",
    "ExtractionResult",
    "PageResult",
]

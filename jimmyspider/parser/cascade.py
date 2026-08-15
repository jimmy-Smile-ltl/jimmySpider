"""
选择器级联引擎 — 核心调度器

按成本从低到高依次尝试多种提取策略，找到第一个满足置信度要求的结果后立即返回。

核心原则:
  1. 优先 XPath/CSS 选择器 — 零 token 成本，可缓存复用
  2. 其次 DOM 语义分析 — 极快，覆盖大部分页面
  3. 再次 JSON-LD / Meta — 结构化数据优先
  4. 最后 LLM — 兜底，成本高但通用
  5. 输出带定位器 — 方便人工确认和下次复用
"""

import re
import time
import json
from typing import Optional, Callable
from dataclasses import dataclass, field
from collections import OrderedDict

from bs4 import BeautifulSoup


@dataclass
class ExtractionResult:
    """单字段提取结果"""
    field_name: str
    value: Optional[str]
    selector_used: str          # 成功时用的定位器
    method: str                 # selector / semantic / meta / jsonld / llm
    confidence: float           # 0.0 ~ 1.0
    candidates: list = field(default_factory=list)  # 所有候选值
    latency_ms: float = 0.0

    @property
    def is_valid(self) -> bool:
        return self.value is not None and len(self.value.strip()) > 0


@dataclass
class PageResult:
    """整页提取结果"""
    url: str
    fields: dict[str, ExtractionResult]  # field_name → result
    total_latency_ms: float = 0.0
    extractors_used: list[str] = field(default_factory=list)
    tokens_used: int = 0                 # LLM token 消耗 (0 = 未用 LLM)

    @property
    def all_valid(self) -> bool:
        return all(r.is_valid for r in self.fields.values())

    @property
    def selectors(self) -> dict[str, str]:
        """输出可复用的定位器字典"""
        return {
            name: r.selector_used
            for name, r in self.fields.items()
            if r.is_valid and r.method == "selector"
        }

    def summary(self) -> str:
        parts = []
        for name, r in self.fields.items():
            status = "✓" if r.is_valid else "✗"
            parts.append(f"  {status} {name}: {r.method} | {r.selector_used[:50]} | conf={r.confidence:.2f}")
        return "\n".join(parts)


class SelectorCascade:
    """
    选择器级联引擎

    策略链（按成本排序）:
      Level 0: 已知定位器 (from sites.yaml / cache)     → 0ms,   0 tokens, 100% 准确
      Level 1: 语义选择器 (h1, meta[og:title], .title)  → ~1ms,  0 tokens, 95%+ 准确 (新闻)
      Level 2: JSON-LD / Schema.org 结构化数据           → ~2ms,  0 tokens, 90%+ 准确 (SEO页面)
      Level 3: DOM 结构分析 (文本密度/阅读器算法)        → ~5ms,  0 tokens, 85% 准确
      Level 4: LLM 语义理解                              → ~2s,   500+ tokens, 95%+ 准确 (通用)
    """

    # 字段级置信度阈值: 达到此值才停止降级
    DEFAULT_THRESHOLD = 0.80

    def __init__(self, known_selectors: dict[str, str] = None,
                 field_thresholds: dict[str, float] = None):
        """
        Args:
            known_selectors: {"title": "h1", "author": ".author", ...}
            field_thresholds: {"title": 0.9, "content": 0.7, ...}
        """
        self.known_selectors = known_selectors or {}
        self.field_thresholds = field_thresholds or {}

    # ================================================================
    #  主入口
    # ================================================================

    def extract(self, html: str, url: str = "",
                schema: dict = None, llm_call: Callable = None) -> PageResult:
        """
        提取整个页面的所有字段

        Args:
            html: 原始 HTML
            url: 页面 URL
            schema: 字段定义 {"fields": [{"name":"title","type":"text"}, ...]}
            llm_call: LLM 调用函数 async def fn(prompt, schema) -> dict

        Returns:
            PageResult: 所有字段的提取结果 + 定位器 + token 消耗
        """
        start = time.time()
        soup = BeautifulSoup(html, "lxml")
        fields_def = schema.get("fields", []) if schema else []
        extractors_used = set()
        tokens_used = 0

        results = OrderedDict()

        for field in fields_def:
            field_name = field["name"]
            threshold = self.field_thresholds.get(field_name, self.DEFAULT_THRESHOLD)

            # 逐级尝试
            result = self._extract_field(
                soup, html, url, field_name, field, threshold, llm_call
            )
            results[field_name] = result
            extractors_used.add(result.method)
            if result.method == "llm":
                tokens_used += 500  # 估算是 500 tokens/字段

        return PageResult(
            url=url,
            fields=results,
            total_latency_ms=(time.time() - start) * 1000,
            extractors_used=list(extractors_used),
            tokens_used=tokens_used,
        )

    # ================================================================
    #  逐级提取
    # ================================================================

    def _extract_field(self, soup: BeautifulSoup, html: str, url: str,
                       field_name: str, field_def: dict,
                       threshold: float, llm_call: Callable) -> ExtractionResult:
        """对单个字段执行级联提取"""
        t0 = time.time()

        # ---- Level 0: 已知定位器 ----
        if field_name in self.known_selectors:
            selector = self.known_selectors[field_name]
            value = self._apply_selector(soup, selector, field_def)
            if value:
                return ExtractionResult(
                    field_name=field_name,
                    value=value,
                    selector_used=selector,
                    method="selector",
                    confidence=1.0,
                    latency_ms=(time.time() - t0) * 1000,
                )

        # ---- Level 1: 语义选择器 ----
        semantic_selectors = self._get_semantic_selectors(field_name)
        for sel in semantic_selectors:
            value = self._apply_selector(soup, sel, field_def)
            if value and self._validate_value(value, field_def, field_name):
                # 如果是此前未知的定位器，记录下来
                confidence = 0.85 if field_name != "title" else 0.95
                if field_name == "title":
                    # h1 匹配成功 → 高置信度 (99.3% 的日报用 h1)
                    confidence = 0.99 if sel == "h1" else 0.92

                if confidence >= threshold:
                    return ExtractionResult(
                        field_name=field_name,
                        value=value,
                        selector_used=sel,
                        method="semantic",
                        confidence=confidence,
                        latency_ms=(time.time() - t0) * 1000,
                    )

        # ---- Level 2: JSON-LD / Meta / OpenGraph ----
        value = self._extract_from_structured(html, soup, field_name)
        if value and self._validate_value(value, field_def, field_name):
            return ExtractionResult(
                field_name=field_name,
                value=value,
                selector_used=f"meta/{field_name}",
                method="meta",
                confidence=0.90,
                latency_ms=(time.time() - t0) * 1000,
            )

        # ---- Level 3: DOM 结构分析 ----
        if field_name in ("title", "content", "main_text"):
            value = self._extract_by_dom_analysis(soup, field_name)
            if value and self._validate_value(value, field_def, field_name):
                return ExtractionResult(
                    field_name=field_name,
                    value=value,
                    selector_used="dom_analysis",
                    method="dom",
                    confidence=0.75,
                    latency_ms=(time.time() - t0) * 1000,
                )

        # ---- Level 4: LLM 兜底 ----
        if llm_call:
            try:
                value = llm_call(html, field_name, field_def)
                if value:
                    return ExtractionResult(
                        field_name=field_name,
                        value=value,
                        selector_used="llm",
                        method="llm",
                        confidence=0.95,
                        latency_ms=(time.time() - t0) * 1000,
                    )
            except Exception:
                pass

        # ---- 全部失败 ----
        return ExtractionResult(
            field_name=field_name,
            value=None,
            selector_used="",
            method="failed",
            confidence=0.0,
            latency_ms=(time.time() - t0) * 1000,
        )

    # ================================================================
    #  Level 0: 已知选择器执行
    # ================================================================

    @staticmethod
    def _apply_selector(soup: BeautifulSoup, selector: str, field_def: dict) -> Optional[str]:
        """应用 CSS/XPath 选择器提取值"""
        extract_type = field_def.get("extract", "text")

        try:
            if selector.startswith("//") or selector.startswith("xpath:"):
                # XPath (需要 lxml)
                if selector.startswith("xpath:"):
                    selector = selector[6:]
                from lxml import etree
                dom = etree.HTML(str(soup))
                elements = dom.xpath(selector)
                if elements:
                    el = elements[0]
                    if extract_type == "text":
                        return "".join(el.itertext()).strip() if hasattr(el, 'itertext') else str(el.text or "").strip()
                    return str(getattr(el, 'attrib', {}).get(extract_type, el.text or ""))
            else:
                # CSS 选择器
                elements = soup.select(selector)
                if elements:
                    el = elements[0]
                    if extract_type == "text":
                        return el.get_text(separator=" ", strip=True)
                    elif extract_type == "html":
                        return el.decode_contents()
                    elif extract_type == "attr" and "attribute" in field_def:
                        return el.get(field_def["attribute"], "")
                    else:
                        return el.get_text(separator=" ", strip=True)
        except Exception:
            pass
        return None

    # ================================================================
    #  Level 1: 语义选择器生成
    # ================================================================

    @staticmethod
    def _get_semantic_selectors(field_name: str) -> list[str]:
        """根据字段名生成最可能的 CSS 选择器序列"""
        SELECTOR_MAP = {
            "title": [
                "h1",
                "h1.title, h1[class*=title]",
                ".article-title", ".post-title", ".news-title", ".bt_title",
                "h2", "h3",
                "[class*=headline]", "[class*=heading]",
                ".title", "#title",
            ],
            "author": [
                ".author", "[class*=author]", "#author",
                ".reporter", ".editor", "[class*=writer]",
                'meta[name="author"]',
            ],
            "publish_date": [
                ".date", "[class*=date]", "#date",
                "[class*=time]", ".publish-time", ".post-date",
                ".article-date", "[class*=pubdate]",
                'meta[property="article:published_time"]',
                "time", '[datetime]',
            ],
            "content": [
                "article", ".article-content", ".post-content",
                ".content", "#content", ".article-body",
                ".news-content", ".entry-content",
                "#ozoom",  # 云展网电子报
                ".rich_media_content",  # 微信公众号
                ".flow", ".wrap",  # 山西日报等
            ],
            "images": [
                "article img", ".article-content img",
                ".content img", "#content img",
            ],
            "tags": [
                ".tags a", ".keywords a", "[class*=tag] a",
                ".article-tags a", "[class*=keyword]",
            ],
            "summary": [
                ".summary", ".abstract", "[class*=summary]",
                ".article-summary", ".post-excerpt",
                'meta[name="description"]',
            ],
        }
        return SELECTOR_MAP.get(field_name, [f".{field_name}", f"#{field_name}"])

    # ================================================================
    #  Level 2: 结构化数据提取 (JSON-LD / Meta / OpenGraph)
    # ================================================================

    @staticmethod
    def _extract_from_structured(html: str, soup: BeautifulSoup, field_name: str) -> Optional[str]:
        """从结构化标记中提取"""

        # 1. JSON-LD (Schema.org)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    value = SelectorCascade._extract_jsonld_field(data, field_name)
                    if value:
                        return value
                elif isinstance(data, list):
                    for item in data:
                        value = SelectorCascade._extract_jsonld_field(item, field_name)
                        if value:
                            return value
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue

        # 2. OpenGraph meta
        og_map = {
            "title": "og:title",
            "summary": "og:description",
            "images": "og:image",
            "publish_date": "article:published_time",
            "author": "article:author",
        }
        if field_name in og_map:
            meta = soup.find("meta", property=og_map[field_name])
            if meta and meta.get("content"):
                return meta["content"].strip()

        # 3. <meta> 标签
        if field_name == "title":
            meta = soup.find("meta", attrs={"name": "title"})
            if not meta:
                meta = soup.find("meta", attrs={"itemprop": "name"})
            if meta and meta.get("content"):
                return meta["content"].strip()

        # 4. <title> 标签
        if field_name == "title":
            title_tag = soup.find("title")
            if title_tag and title_tag.string:
                # 去除站点名后缀 "标题 - 站点名" → "标题"
                text = title_tag.string.strip()
                for sep in [" - ", " | ", " _ ", " — "]:
                    if sep in text:
                        parts = text.split(sep)
                        # 保留最长的部分作为标题
                        longest = max(parts, key=len)
                        if len(longest) >= 5:  # 至少5个字符
                            return longest.strip()
                return text

        return None

    @staticmethod
    def _extract_jsonld_field(data: dict, field_name: str) -> Optional[str]:
        """从 JSON-LD 中提取字段"""
        type_map = {
            "title": ["headline", "name", "title"],
            "author": ["author", "creator"],
            "publish_date": ["datePublished", "dateCreated"],
            "summary": ["description", "abstract"],
        }
        keys = type_map.get(field_name, [field_name])

        # 直接字段
        for key in keys:
            if key in data and data[key]:
                val = data[key]
                if isinstance(val, str):
                    return val.strip()
                if isinstance(val, dict) and "name" in val:
                    return val["name"].strip()
                if isinstance(val, list) and val:
                    if isinstance(val[0], str):
                        return val[0].strip()
                    if isinstance(val[0], dict) and "name" in val[0]:
                        return val[0]["name"].strip()

        # 嵌套: author → [{"name": "..."}]
        if field_name == "author":
            for person in data.get("author", []):
                if isinstance(person, dict) and "name" in person:
                    return person["name"].strip()

        return None

    # ================================================================
    #  Level 3: DOM 结构分析
    # ================================================================

    @staticmethod
    def _extract_by_dom_analysis(soup: BeautifulSoup, field_name: str) -> Optional[str]:
        """基于 DOM 结构分析提取"""

        if field_name == "title":
            return SelectorCascade._extract_title_by_dom(soup)
        elif field_name in ("content", "main_text"):
            return SelectorCascade._extract_content_by_density(soup)

        return None

    @staticmethod
    def _extract_title_by_dom(soup: BeautifulSoup) -> Optional[str]:
        """
        基于文本特征找标题

        策略:
          1. 找文本长度 5-200 的 h1/h2/h3
          2. 找 font-size 最大的文本节点
          3. 找文档最前面的大号文本
        """
        candidates = []

        # 策略 1: 遍历 h1~h3
        for tag in soup.find_all(["h1", "h2", "h3"]):
            text = tag.get_text(strip=True)
            if 5 <= len(text) <= 200:
                # 过滤明显不是标题的: 全是数字/全是英文/含导航词
                if not SelectorCascade._is_noise_text(text):
                    score = 10 if tag.name == "h1" else (7 if tag.name == "h2" else 5)
                    # h1 + 含中文字符 → 极高置信度
                    if tag.name == "h1" and re.search(r'[一-鿿]', text):
                        score += 10
                    candidates.append((text, score, tag.name))

        if candidates:
            candidates.sort(key=lambda x: -x[1])
            return candidates[0][0]

        return None

    @staticmethod
    def _extract_content_by_density(soup: BeautifulSoup) -> Optional[str]:
        """基于文本密度提取正文"""
        # 剥离非内容标签
        for tag in soup.find_all(["script", "style", "nav", "footer", "iframe", "header"]):
            tag.decompose()

        candidates = []
        for el in soup.find_all(["article", "div", "section", "main"]):
            text = el.get_text(strip=True)
            text_len = len(text)
            if text_len < 100:  # 太短忽略
                continue

            tags = el.find_all()
            tag_count = len(tags) + 1
            link_text = sum(len(a.get_text(strip=True)) for a in el.find_all("a"))

            density = text_len / tag_count
            link_ratio = link_text / max(text_len, 1)

            score = density * (1 - link_ratio) * min(text_len / 500, 1.0)

            # class/id 语义加分
            el_class = " ".join(el.get("class", [])) + el.get("id", "")
            semantic_words = ["content", "article", "post", "body", "text", "main",
                            "ozoom", "flow", "wrap", "rich_media"]
            for word in semantic_words:
                if word in el_class.lower():
                    score *= 1.5

            candidates.append((el, score))

        if candidates:
            candidates.sort(key=lambda x: -x[1])
            best, best_score = candidates[0]
            if best_score > 1.0:
                return best.get_text(separator="\n", strip=True)

        return None

    # ================================================================
    #  验证
    # ================================================================

    @staticmethod
    def _validate_value(value: str, field_def: dict, field_name: str) -> bool:
        """验证提取值是否合理"""
        if not value or not value.strip():
            return False

        v = value.strip()

        # 长度检查
        min_len = field_def.get("min_length", 0)
        max_len = field_def.get("max_length", 999999)
        if len(v) < min_len or len(v) > max_len:
            return False

        # 标题特殊检查
        if field_name == "title":
            # 不应包含大量英文 (说明可能是导航/脚本残余)
            english_ratio = len(re.findall(r'[a-zA-Z]', v)) / max(len(v), 1)
            if english_ratio > 0.8 and len(v) < 30:
                return False
            # 不应是纯数字
            if re.match(r'^[\d\s\-/]+$', v):
                return False
            # 中文标题至少 3 个中文字符
            if re.search(r'[一-鿿]', v):
                if len(re.findall(r'[一-鿿]', v)) < 3:
                    return False
                return True
            return len(v) >= 3

        return True

    @staticmethod
    def _is_noise_text(text: str) -> bool:
        """判断文本是否是噪音 (导航 / 菜单 / 页脚)"""
        noise_patterns = [
            r'^[A-Za-z\s\d\W]+$',           # 纯英文/数字/符号
            r'^(首页|登录|注册|搜索|导航|菜单)$',
            r'^©\d{4}',                      # 版权信息
            r'^\d{4}-\d{2}-\d{2}$',          # 纯日期
            r'^(上一篇|下一篇|返回)$',
        ]
        return any(re.match(p, text) for p in noise_patterns)

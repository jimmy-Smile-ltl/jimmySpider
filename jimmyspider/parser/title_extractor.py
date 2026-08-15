"""
中文标题提取器 — 针对日报/新闻网站优化

基于 1035 个真实日报站点的统计数据:
  - 99.3% 用 h1 作为标题
  - 0.4% 用 h2
  - 0.1% 用 h3
  - <0.2% 需要其他方式

定位器优先级:
  Level 0: sites.yaml 已知选择器 (成本=0, 准确率=100%)
  Level 1: h1 → h1.title → .article-title → .post-title (成本=~0.5ms)
  Level 2: meta[og:title] → JSON-LD → <title> tag (成本=~1ms)
  Level 3: DOM 文本特征分析 (成本=~3ms)
  Level 4: LLM (成本=~2s + 200 tokens)

核心优化:
  1. 成功提取后自动记录 selector → 写入 sites.yaml
  2. 同站点后续页面直接复用 → 0 token 成本
  3. 对中文标题做专门验证 (中文字符数/长度/噪音过滤)
"""

import re
import time
from typing import Optional, Callable
from bs4 import BeautifulSoup, Tag

from .cascade import ExtractionResult


class TitleExtractor:
    """
    中文标题提取器

    针对新闻/日报类型页面高度优化。
    基于 1035 个真实日报站点的 selector 分布数据。
    """

    # 统计自 sites.yaml 的 title_selector 分布
    SELECTOR_DISTRIBUTION = {
        "h1": 1028,  # 99.3%
        "h2": 4,     # 0.4%
        "h3": 1,     # 0.1%
        "other": 2,  # <0.2%
    }

    # 语义选择器序列（按命中率排序）
    TITLE_SELECTORS = [
        # Tier 1: 标准语义标签 (覆盖 99.7%)
        "h1",
        "h1[class*=title], h1[class*=headline]",
        "h2",
        "h3",
        # Tier 2: 语义化 class 名
        ".article-title",
        ".post-title",
        ".news-title",
        ".bt_title",
        "[class*=article-title]",
        "[class*=post-title]",
        "[class*=news-title]",
        # Tier 3: 通用 class/id
        ".title",
        "#title",
        "[class*=headline]",
        "[class*=heading]",
        # Tier 4: 日报专用（电子报系统）
        "h1",           # 云展网 / 大多数电子报
        ".detail-title",  # 部分省级日报
        ".main-title",    # 部分市级日报
    ]

    def __init__(self):
        self.cache: dict[str, str] = {}  # domain → selector

    def extract(self, html: str, url: str = "",
                known_selector: str = "",
                llm_call: Callable = None) -> ExtractionResult:
        """
        提取中文标题

        Args:
            html: 原始 HTML
            url: 页面 URL
            known_selector: 已知的定位器 (来自 sites.yaml)
            llm_call: LLM 兜底函数

        Returns:
            ExtractionResult: 包含标题值 + 定位器 + 置信度 + 候选列表
        """
        t0 = time.time()
        domain = self._get_domain(url)
        soup = BeautifulSoup(html, "lxml")

        # ---- Level 0: 已知定位器 (缓存或 sites.yaml) ----
        selector = known_selector or self.cache.get(domain)
        if selector:
            value = self._try_selector(soup, selector)
            if value and self._is_valid_chinese_title(value):
                return ExtractionResult(
                    field_name="title",
                    value=value,
                    selector_used=selector,
                    method="selector",
                    confidence=1.0,
                    latency_ms=(time.time() - t0) * 1000,
                )

        # ---- Level 1: 语义选择器尝试 (0 token 成本) ----
        all_candidates = []
        for sel in self.TITLE_SELECTORS:
            value = self._try_selector(soup, sel)
            if value:
                confidence = self._score_title(value, sel)
                all_candidates.append((value, sel, confidence))
                if confidence >= 0.80:  # 达到阈值立即返回
                    # 记录到缓存
                    if domain:
                        self.cache[domain] = sel
                    return ExtractionResult(
                        field_name="title",
                        value=value,
                        selector_used=sel,
                        method="semantic",
                        confidence=confidence,
                        candidates=[(v, s) for v, s, c in all_candidates],
                        latency_ms=(time.time() - t0) * 1000,
                    )

        # ---- Level 2: 结构化数据 ----
        value = self._extract_from_meta(soup)
        if value and self._is_valid_chinese_title(value):
            return ExtractionResult(
                field_name="title",
                value=value,
                selector_used="meta:og:title / jsonld",
                method="meta",
                confidence=0.88,
                candidates=[(v, s) for v, s, c in all_candidates],
                latency_ms=(time.time() - t0) * 1000,
            )

        # ---- Level 3: DOM 特征 ----
        value = self._extract_by_features(soup)
        if value and self._is_valid_chinese_title(value):
            return ExtractionResult(
                field_name="title",
                value=value,
                selector_used="dom_features",
                method="dom",
                confidence=0.70,
                candidates=[(v, s) for v, s, c in all_candidates],
                latency_ms=(time.time() - t0) * 1000,
            )

        # ---- Level 4: LLM ----
        if llm_call:
            try:
                value = llm_call(html, "title", {"type": "text"})
                if value:
                    return ExtractionResult(
                        field_name="title",
                        value=value,
                        selector_used="llm",
                        method="llm",
                        confidence=0.95,
                        latency_ms=(time.time() - t0) * 1000,
                    )
            except Exception:
                pass

        # ---- 全部失败: 返回最高分候选 ----
        if all_candidates:
            best = max(all_candidates, key=lambda x: x[2])
            return ExtractionResult(
                field_name="title",
                value=best[0],
                selector_used=best[1],
                method="semantic",
                confidence=best[2],
                candidates=[(v, s) for v, s, c in all_candidates],
                latency_ms=(time.time() - t0) * 1000,
            )

        return ExtractionResult(
            field_name="title", value=None, selector_used="",
            method="failed", confidence=0.0,
            latency_ms=(time.time() - t0) * 1000,
        )

    # ================================================================
    #  选择器执行
    # ================================================================

    @staticmethod
    def _try_selector(soup: BeautifulSoup, selector: str) -> Optional[str]:
        """尝试一个 CSS 选择器"""
        try:
            elements = soup.select(selector)
            if elements:
                el = elements[0]
                return el.get_text(separator=" ", strip=True)
        except Exception:
            pass
        return None

    # ================================================================
    #  结构化数据提取
    # ================================================================

    @staticmethod
    def _extract_from_meta(soup: BeautifulSoup) -> Optional[str]:
        """从 meta 标签提取标题"""
        import json

        # JSON-LD headline
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                headline = (data.get("headline") or
                           data.get("name") or
                           data.get("title"))
                if headline and isinstance(headline, str):
                    return headline.strip()
            except Exception:
                pass

        # og:title
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()

        # <title> tag
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            text = title_tag.string.strip()
            for sep in [" - ", " | ", " _ ", " — ", "–"]:
                if sep in text:
                    parts = [p.strip() for p in text.split(sep) if len(p.strip()) >= 4]
                    if parts:
                        longest = max(parts, key=len)
                        return longest
            return text

        return None

    # ================================================================
    #  DOM 特征提取 (Layer 3, 兜底)
    # ================================================================

    @staticmethod
    def _extract_by_features(soup: BeautifulSoup) -> Optional[str]:
        """基于 DOM 特征找最像标题的文本"""
        candidates = []

        for el in soup.find_all(["h1", "h2", "h3", "div", "span", "p"]):
            text = el.get_text(strip=True)
            if not text or len(text) < 5 or len(text) > 200:
                continue

            score = TitleExtractor._score_title(text, el.name)

            # class/id 语义加分
            el_id = el.get("id", "")
            el_class = " ".join(el.get("class", []))
            combined = (el_id + " " + el_class).lower()
            semantic = ["title", "headline", "heading", "bt_title", "article-title"]
            for word in semantic:
                if word in combined:
                    score += 1.0

            # 位置加分: 越靠前越可能是标题
            if el.sourceline and el.sourceline < 50:
                score += 0.3

            if score >= 1.5:
                candidates.append((text, score, el.name))

        if candidates:
            candidates.sort(key=lambda x: -x[1])
            return candidates[0][0]

        return None

    # ================================================================
    #  中文标题评分
    # ================================================================

    @staticmethod
    def _score_title(text: str, tag_name: str = "") -> float:
        """
        对候选标题打分 (0-10)

        评分维度:
          1. 长度适中 (10-80 字)           → +3
          2. 含中文字符且占比 >50%         → +2
          3. 标签是 h1                      → +3
          4. 不含纯导航/日期格式            → +1
          5. 不含过多标点符号               → +1
        """
        if not text:
            return 0.0
        score = 0.0
        text_len = len(text)

        # 1. 长度
        if 10 <= text_len <= 80:
            score += 3
        elif 5 <= text_len <= 150:
            score += 1.5

        # 2. 中文字符
        chinese_chars = len(re.findall(r'[一-鿿㐀-䶿]', text))
        chinese_ratio = chinese_chars / max(text_len, 1)
        if chinese_ratio >= 0.5:
            score += 2 + min(chinese_ratio, 1.0)  # 中文越多越好
        elif chinese_ratio < 0.3:
            score -= 1  # 惩罚

        # 3. 标签权重
        if tag_name == "h1":
            score += 3
        elif tag_name == "h2":
            score += 2
        elif tag_name == "h3":
            score += 1

        # 4. 噪音惩罚
        noise_phrases = ["首页", "登录", "注册", "导航", "版权", "上一篇", "下一篇",
                        "返回顶部", "设为首页", "加入收藏"]
        for phrase in noise_phrases:
            if phrase in text:
                score -= 2
                break

        # 5. 标点过多惩罚
        punct_ratio = len(re.findall(r'[，。！？、；：""''（）【】《》]', text)) / max(text_len, 1)
        if punct_ratio > 0.3:
            score -= 1

        # 6. 纯数字/日期惩罚
        if re.match(r'^[\d\s\-/.年月日:：]+$', text):
            score -= 3

        return max(score, 0.0) / 10.0  # 归一化到 0-1

    @staticmethod
    def _is_valid_chinese_title(text: str) -> bool:
        """验证中文标题是否有效"""
        if not text or not text.strip():
            return False
        t = text.strip()
        if len(t) < 3:
            return False
        # 至少含 2 个中文字符
        if len(re.findall(r'[一-鿿]', t)) < 2:
            return False
        # 不是纯导航文本
        noise = ["首页", "上一页", "下一页", "上一篇", "下一篇", "返回", "登录", "注册"]
        if t in noise:
            return False
        return True

    @staticmethod
    def _get_domain(url: str) -> str:
        """从 URL 提取域名"""
        import re
        match = re.search(r'https?://([^/]+)', url)
        return match.group(1) if match else ""

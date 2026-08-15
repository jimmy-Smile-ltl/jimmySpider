"""
正文/内容提取器

结合:
  1. sites.yaml 已知选择器 (content_selector)
  2. 文本密度算法 (Modified Readability)
  3. 云展网 #ozoom 特殊处理
  4. 已有的 extract_content / extract_content_recursively 算法
"""

import re
import time
from typing import Optional
from bs4 import BeautifulSoup, Tag

from .cascade import ExtractionResult


class ContentExtractor:
    """正文内容提取器"""

    CONTENT_SELECTORS = [
        # 云展网电子报 (sites.yaml 最常用)
        "#ozoom",
        # 通用语义
        "article",
        ".article-content",
        ".post-content",
        ".content",
        "#content",
        ".article-body",
        ".news-content",
        ".entry-content",
        # 微信公众号
        ".rich_media_content",
        # 省级日报
        ".flow", ".wrap",
        # 通用
        ".detail-content",
        ".main-content",
        ".text-content",
    ]

    def __init__(self):
        self.cache: dict[str, str] = {}

    def extract(self, html: str, url: str = "",
                known_selector: str = "") -> ExtractionResult:
        t0 = time.time()
        domain = self._get_domain(url)
        soup = BeautifulSoup(html, "lxml")

        # Level 0: 已知定位器
        selector = known_selector or self.cache.get(domain)
        if selector:
            value = self._extract_by_selector(soup, selector)
            if value and len(value) > 100:
                if domain:
                    self.cache[domain] = selector
                return ExtractionResult(
                    field_name="content",
                    value=value,
                    selector_used=selector,
                    method="selector",
                    confidence=1.0,
                    latency_ms=(time.time() - t0) * 1000,
                )

        # Level 1: 语义选择器
        for sel in self.CONTENT_SELECTORS:
            value = self._extract_by_selector(soup, sel)
            if value and len(value) > 100:
                confidence = 0.90 if sel == "#ozoom" else 0.82
                if domain:
                    self.cache[domain] = sel
                return ExtractionResult(
                    field_name="content",
                    value=value,
                    selector_used=sel,
                    method="semantic",
                    confidence=confidence,
                    latency_ms=(time.time() - t0) * 1000,
                )

        # Level 2: 文本密度算法
        value = self._extract_by_density(soup)
        if value and len(value) > 80:
            return ExtractionResult(
                field_name="content",
                value=value,
                selector_used="text_density",
                method="dom",
                confidence=0.70,
                latency_ms=(time.time() - t0) * 1000,
            )

        # Level 3: 递归提取 (已有算法)
        value = self._extract_recursive(soup)
        if value and len(value) > 50:
            return ExtractionResult(
                field_name="content",
                value=value,
                selector_used="recursive_extract",
                method="dom",
                confidence=0.60,
                latency_ms=(time.time() - t0) * 1000,
            )

        # 兜底: 取 body 文本
        body = soup.find("body")
        if body:
            return ExtractionResult(
                field_name="content",
                value=body.get_text(separator="\n", strip=True)[:50000],
                selector_used="body",
                method="dom",
                confidence=0.40,
                latency_ms=(time.time() - t0) * 1000,
            )

        return ExtractionResult(
            field_name="content", value=None, selector_used="",
            method="failed", confidence=0.0,
            latency_ms=(time.time() - t0) * 1000,
        )

    # ================================================================
    #  选择器提取
    # ================================================================

    @staticmethod
    def _extract_by_selector(soup: BeautifulSoup, selector: str) -> Optional[str]:
        """用选择器提取后做清洗"""
        try:
            el = soup.select_one(selector)
            if not el:
                return None

            # 剥离内部不需要的标签
            for bad in el.find_all(["script", "style", "iframe", "nav"]):
                bad.decompose()

            # <br> → 换行
            for br in el.find_all("br"):
                br.replace_with("\n")

            text = el.get_text(separator="\n", strip=True)

            # 清洗
            text = re.sub(r'[ \t]{2,}', ' ', text)      # 多个空格合并
            text = re.sub(r'\n{3,}', '\n\n', text)       # 多个换行合并
            return text.strip()

        except Exception:
            return None

    # ================================================================
    #  文本密度算法
    # ================================================================

    @staticmethod
    def _extract_by_density(soup: BeautifulSoup) -> Optional[str]:
        """文本密度算法: 找正文容器"""
        # 剥离噪音标签
        for bad in soup.find_all(["script", "style", "nav", "footer", "header",
                                   "iframe", "noscript"]):
            bad.decompose()

        candidates = []
        for el in soup.find_all(["article", "div", "section", "main"]):
            text = el.get_text(strip=True)
            if len(text) < 150:
                continue

            tags = el.find_all()
            tag_count = len(tags) + 1
            link_text = sum(len(a.get_text(strip=True)) for a in el.find_all("a"))

            density = len(text) / tag_count
            link_ratio = link_text / max(len(text), 1)

            score = density * (1 - link_ratio) * min(len(text) / 1000, 2.0)

            # 语义加分
            el_class = " ".join(el.get("class", [])) + el.get("id", "")
            semantic = ["content", "article", "post", "body", "text", "main",
                       "ozoom", "flow", "wrap", "rich_media", "detail"]
            for word in semantic:
                if word in el_class.lower():
                    score *= 1.3

            candidates.append((el, score))

        if candidates:
            candidates.sort(key=lambda x: -x[1])
            best, score = candidates[0]
            if score > 0.5:
                return ContentExtractor._clean_text(best)

        return None

    # ================================================================
    #  递归提取 (迁移自 handleSoup.py)
    # ================================================================

    @staticmethod
    def _extract_recursive(soup: BeautifulSoup) -> Optional[str]:
        """递归提取正文 (详见 util/handleSoup.py _recursive_extract)"""
        from copy import deepcopy
        soup_copy = deepcopy(soup)

        for br in soup_copy.find_all("br"):
            br.replace_with("\n")

        SIGNIFICANT_TAGS = {"p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "button", "div"}

        def recursive(el: Tag) -> str:
            if el.name in SIGNIFICANT_TAGS and len(el.find_all(list(SIGNIFICANT_TAGS))) <= 1:
                return el.get_text(strip=True) + "\n"
            parts = []
            for child in el.children:
                if isinstance(child, Tag):
                    parts.append(recursive(child))
            return "".join(parts)

        text = recursive(soup_copy)
        text = re.sub(r'[ \t]{2,}', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    # ================================================================
    #  工具
    # ================================================================

    @staticmethod
    def _clean_text(el: Tag) -> str:
        for br in el.find_all("br"):
            br.replace_with("\n")
        text = el.get_text(separator="\n", strip=True)
        text = re.sub(r'[ \t]{2,}', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    @staticmethod
    def _get_domain(url: str) -> str:
        match = re.search(r'https?://([^/]+)', url)
        return match.group(1) if match else ""

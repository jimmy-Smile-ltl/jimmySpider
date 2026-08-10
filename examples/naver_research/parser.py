"""
Naver Finance 研究报告数据提取模块
=====================================
本文件属于 examples/naver_research 示例：
  - 不依赖 jimmyspider 框架，纯 BeautifulSoup + 标准库，
    作为独立解析模块被 spider.py 导入（from parser import parse_page, get_total_pages, TYPE_LIST）
  - 演示「同一站点多种页面结构」的解析函数族设计：
    6 个 parse_xxx 函数 + _PARSER_MAP 路由表 + 统一入口 parse_page()
  - __main__ 为开发期自测：读取本地 HTML 快照验证解析结果
    （快照文件未随示例发布，如需自测请自行保存对应页面）

针对 6 种报告类型，基于真实 HTML 结构定制解析函数。

【各类型实测表格结构】
╔══════════════════╦════╦══════════════════════════════════════════════╦════════════╗
║ 类型              ║ 列 ║ 列顺序                                       ║ 唯一特征   ║
╠══════════════════╬════╬══════════════════════════════════════════════╬════════════╣
║ 시황정보 리포트    ║  5 ║ 제목 | 증권사 | 첨부 | 작성일 | 조회수       ║ -          ║
║ 투자정보 리포트    ║  5 ║ 제목 | 증권사 | 첨부 | 작성일 | 조회수       ║ -          ║
║ 종목분析 리포트    ║  6 ║ 종목명 | 제목 | 증권사 | 첨부 | 작성일 | 조회수 ║ 종목명+종목URL ║
║ 산업분析 리포트    ║  6 ║ 분류 | 제목 | 증권사 | 첨부 | 작성일 | 조회수  ║ 분류(업종) ║
║ 경제분析 리포트    ║  5 ║ 제목 | 증권사 | 첨부 | 작성일 | 조회수       ║ -          ║
║ 채권분析 리포트    ║  5 ║ 제목 | 증권사 | 첨부 | 작성일 | 조회수       ║ -          ║
╚══════════════════╩════╩══════════════════════════════════════════════╩════════════╝

【TR 行类型识别】
  - 表头行:  含 <th>，跳过
  - 分割行:  <td colspan=N>（class 含 blank_07/08/09, division_line 等），跳过
  - 数据行:  多个普通 <td>，第一列（或第二列）含 <a> 链接

【日期格式统一】
  YY.MM.DD  → 20YY-MM-DD   (e.g. 26.04.24 → 2026-04-24)
  YYYY.MM.DD → YYYY-MM-DD  (e.g. 2026.04.24 → 2026-04-24)
"""

import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup, Tag


# ──────────────────────────────────────────────────────────────
# 常量：各类型 URL 路由
# ──────────────────────────────────────────────────────────────

NAVER_BASE = "https://finance.naver.com"

TYPE_LIST = [
    {
        "type_kr":  "시황정보 리포트",
        "type_cn":  "行情信息报告",
        "type_url": "https://finance.naver.com/research/market_info_list.naver",
    },
    {
        "type_kr":  "투자정보 리포트",
        "type_cn":  "投资信息报告",
        "type_url": "https://finance.naver.com/research/invest_list.naver",
    },
    {
        "type_kr":  "종목분석 리포트",
        "type_cn":  "个股分析报告",
        "type_url": "https://finance.naver.com/research/company_list.naver",
    },
    {
        "type_kr":  "산업분析 리포트",
        "type_cn":  "行业分析报告",
        "type_url": "https://finance.naver.com/research/industry_list.naver",
    },
    {
        "type_kr":  "경제분析 리포트",
        "type_cn":  "经济分析报告",
        "type_url": "https://finance.naver.com/research/economy_list.naver",
    },
    {
        "type_kr":  "채권분析 리포트",
        "type_cn":  "债券分析报告",
        "type_url": "https://finance.naver.com/research/debenture_list.naver",
    },
]


# ──────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────

def normalize_date(raw: str) -> str:
    """
    将 Naver 的日期字符串统一转换为 YYYY-MM-DD。

    支持格式：
      YY.MM.DD    → 20YY-MM-DD  (e.g. 26.04.24  → 2026-04-24)
      YYYY.MM.DD  → YYYY-MM-DD  (e.g. 2026.04.24 → 2026-04-24)
      YYYY-MM-DD  → 不变
    """
    s = raw.strip()
    # 匹配 20YY.MM.DD 或 20YY-MM-DD
    m = re.match(r"^(20\d{2})[.\-](\d{2})[.\-](\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # 匹配 YY.MM.DD（两位年）
    m = re.match(r"^(\d{2})[.\-](\d{2})[.\-](\d{2})$", s)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s  # 无法识别时原样返回


def _is_data_row(tds: list) -> bool:
    """
    判断一行 <tr> 是否为数据行（排除表头行、分割行）。
    - 分割行特征：只有 1 个 td，且有 colspan 属性
    """
    if not tds:
        return False
    if tds[0].get("colspan"):
        return False
    return True


def _file_url(td: Tag) -> str | None:
    """从附件列 <td> 提取 PDF 链接（无附件则返回 None）。"""
    a = td.find("a", href=True)
    return a["href"] if a else None


def _abs_url(base_url: str, href: str) -> str:
    """将相对路径转换为绝对 URL。"""
    if not href:
        return ""
    if href.startswith("http"):
        return href
    # href 形如 "company_read.naver?nid=xxx" 或 "/item/main.naver?code=xxx"
    if href.startswith("/"):
        return NAVER_BASE + href
    return urljoin(base_url, href)


# ──────────────────────────────────────────────────────────────
# 各类型解析函数
# ──────────────────────────────────────────────────────────────

def parse_market_info(soup: BeautifulSoup, type_url: str, report_type="行情信息报告") -> list[dict]:
    """
    시황정보 리포트（行情信息报告）
    5列: 제목 | 증권사 | 첨부 | 작성일 | 조회수

    td[0] 제목:   <a href="market_info_read.naver?nid=xxx">标题</a>
    td[1] 증권사: 证券公司名
    td[2] 첨부:   有附件时含 <a href="https://...pdf">
    td[3] 작성일: 日期字符串，格式 YY.MM.DD
    td[4] 조회수: 浏览量（纯文本数字）
    """
    result = []
    for tr in soup.select("table.type_1 tr"):
        tds = tr.select("td")
        if not _is_data_row(tds) or len(tds) < 5:
            continue
        a = tds[0].find("a", href=True)
        if not a:
            continue
        result.append({
            "type":       report_type,
            "title":      a.get_text(strip=True),
            "detail_url": _abs_url(type_url, a["href"]),
            "company":    tds[1].get_text(strip=True),
            "file_url":   _file_url(tds[2]),
            "date":       normalize_date(tds[3].get_text(strip=True)),
            "view_count": tds[4].get_text(strip=True),
        })
    return result


def parse_invest(soup: BeautifulSoup, type_url: str) -> list[dict]:
    """
    투자정보 리포트（投资信息报告）
    5列: 제목 | 증권사 | 첨부 | 작성일 | 조회수
    结构与 market_info 完全相同，复用同一逻辑。

    td[0] 제목:   <a href="invest_read.naver?nid=xxx">标题</a>
    td[1] 증권사: 证券公司名
    td[2] 첨부:   PDF 链接（如有）
    td[3] 작성일: YY.MM.DD
    td[4] 조회수: 浏览量
    """
    return parse_market_info(soup, type_url, report_type="投资信息报告")


def parse_company(soup: BeautifulSoup, type_url: str) -> list[dict]:
    """
    종목분析 리포트（个股分析报告）
    6列: 종목명 | 제목 | 증권사 | 첨부 | 작성일 | 조회수

    td[0] 종목명: <a class="stock_item" href="/item/main.naver?code=xxx" title="종목명">종목명</a>
                  style="padding-left:10"
    td[1] 제목:   <a href="company_read.naver?nid=xxx">标题</a>
                  标题后可能跟 <img class="ico_new"> NEW图标，取文字即可
    td[2] 증권사: 证券公司名
    td[3] 첨부:   PDF 链接（如有）
    td[4] 작성일: YY.MM.DD
    td[5] 조회수: 浏览量

    注：本类型无「목표주가」列（实测为6列非7列）
    """
    result = []
    for tr in soup.select("table.type_1 tr"):
        tds = tr.select("td")
        if not _is_data_row(tds) or len(tds) < 6:
            continue
        title_a = tds[1].find("a", href=True)
        if not title_a:
            continue
        stock_a = tds[0].find("a", href=True)
        result.append({
            "type":        "个股分析报告",
            "stock_name":  tds[0].get_text(strip=True),
            "stock_url":   _abs_url(type_url, stock_a["href"]) if stock_a else None,
            "title":       title_a.get_text(strip=True),
            "detail_url":  _abs_url(type_url, title_a["href"]),
            "company":     tds[2].get_text(strip=True),
            "file_url":    _file_url(tds[3]),
            "date":        normalize_date(tds[4].get_text(strip=True)),
            "view_count":  tds[5].get_text(strip=True),
        })
    return result


def parse_industry(soup: BeautifulSoup, type_url: str) -> list[dict]:
    """
    산업분析 리포트（行业分析报告）
    6列: 분류 | 제목 | 증권사 | 첨부 | 작성일 | 조회수

    td[0] 분류:   行业分类纯文本（e.g. 기타, 조선, 제약, IT）无 <a> 标签
    td[1] 제목:   <a href="industry_read.naver?nid=xxx">标题</a>
    td[2] 증권사: 证券公司名
    td[3] 첨부:   PDF 链接（如有）
    td[4] 작성일: YY.MM.DD
    td[5] 조회수: 浏览量

    注：与 company 同为6列，区分靠 td[0] 是否有 <a>：
        - company:  td[0] 有 <a class="stock_item">
        - industry: td[0] 无 <a>，纯文字
    """
    result = []
    for tr in soup.select("table.type_1 tr"):
        tds = tr.select("td")
        if not _is_data_row(tds) or len(tds) < 6:
            continue
        title_a = tds[1].find("a", href=True)
        if not title_a:
            continue
        result.append({
            "type":       "行业分析报告",
            "category":   tds[0].get_text(strip=True),   # 업종/분류
            "title":      title_a.get_text(strip=True),
            "detail_url": _abs_url(type_url, title_a["href"]),
            "company":    tds[2].get_text(strip=True),
            "file_url":   _file_url(tds[3]),
            "date":       normalize_date(tds[4].get_text(strip=True)),
            "view_count": tds[5].get_text(strip=True),
        })
    return result


def parse_economy(soup: BeautifulSoup, type_url: str) -> list[dict]:
    """
    경제분析 리포트（经济分析报告）
    5列: 제목 | 증권사 | 첨부 | 작성일 | 조회수
    结构与 market_info 完全相同。

    td[0] 제목:   <a href="economy_read.naver?nid=xxx">标题</a>
    td[1] 증권사: 证券公司名
    td[2] 첨부:   PDF 链接（如有）
    td[3] 작성일: YY.MM.DD
    td[4] 조회수: 浏览量
    """
    return parse_market_info(soup, type_url, report_type="经济分析报告")


def parse_debenture(soup: BeautifulSoup, type_url: str) -> list[dict]:
    """
    채권분析 리포트（债券分析报告）
    5列: 제목 | 증권사 | 첨부 | 작성일 | 조회수
    结构与 market_info 完全相同。

    td[0] 제목:   <a href="debenture_read.naver?nid=xxx">标题</a>
    td[1] 증권사: 证券公司名
    td[2] 첨부:   PDF 链接（如有）
    td[3] 작성일: YY.MM.DD
    td[4] 조회수: 浏览量
    """
    return parse_market_info(soup, type_url, report_type="债券分析报告")


# ──────────────────────────────────────────────────────────────
# 统一入口：根据 type_url 自动选择解析函数
# ──────────────────────────────────────────────────────────────

_PARSER_MAP = {
    "market_info_list": parse_market_info,
    "invest_list":      parse_invest,
    "company_list":     parse_company,
    "industry_list":    parse_industry,
    "economy_list":     parse_economy,
    "debenture_list":   parse_debenture,
}


def get_parser(type_url: str):
    """
    根据 type_url 自动匹配对应解析函数。

    Args:
        type_url: 如 "https://finance.naver.com/research/company_list.naver"

    Returns:
        parse_xxx(soup, type_url) -> list[dict]
    """
    for key, fn in _PARSER_MAP.items():
        if key in type_url:
            return fn
    raise ValueError(f"未知的 type_url，无法匹配解析器: {type_url}")


def parse_page(html: str, type_url: str) -> list[dict]:
    """
    主入口：解析一页 HTML，返回该页所有报告记录。

    Args:
        html:     页面 HTML 字符串
        type_url: 该类型的列表页 URL（用于路由解析器 + 拼接绝对路径）

    Returns:
        list[dict]，字段因类型而异，见各 parse_xxx 函数的文档
    """
    soup = BeautifulSoup(html, "html.parser")
    parser = get_parser(type_url)
    return parser(soup, type_url)


def get_total_pages(html: str) -> int:
    """
    从分页导航中提取总页数。
    分页结构：<table class="Nnavi"> ... <td class="pgRR"><a href="?page=N">맨뒤</a>

    Returns:
        总页数（int），找不到时返回 1
    """
    soup = BeautifulSoup(html, "html.parser")
    last_a = soup.select_one("table.Nnavi td.pgRR a")
    if last_a:
        m = re.search(r"page=(\d+)", last_a.get("href", ""))
        if m:
            return int(m.group(1))
    # 兜底：取所有数字页码最大值
    pages = [
        int(a.get_text(strip=True))
        for a in soup.select("table.Nnavi td a")
        if a.get_text(strip=True).isdigit()
    ]
    return max(pages, default=1)


# ──────────────────────────────────────────────────────────────
# 测试：对 6 个实际 HTML 文件验证
# （开发期自测用；html_*.html 快照未随示例发布，需自行保存）
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    TEST_FILES = {
        "https://finance.naver.com/research/market_info_list.naver":
            "html_行情信息报告_market_info_list.html",
        "https://finance.naver.com/research/invest_list.naver":
            "html_投资信息报告_invest_list.html",
        "https://finance.naver.com/research/company_list.naver":
            "html_个股分析报告_company_list.html",
        "https://finance.naver.com/research/industry_list.naver":
            "html_行业分析报告_industry_list.html",
        "https://finance.naver.com/research/economy_list.naver":
            "html_经济分析报告_economy_list.html",
        "https://finance.naver.com/research/debenture_list.naver":
            "html_债券分析报告_debenture_list.html",
    }

    all_results = {}
    for type_url, path in TEST_FILES.items():
        with open(path, encoding="utf-8") as f:
            html = f.read()

        records = parse_page(html, type_url)
        total_pages = get_total_pages(html)
        name = type_url.split("/")[-1].replace("_list.naver", "")

        print(f"\n{'='*60}")
        print(f"  {name}  →  {len(records)} 条记录  (共 {total_pages} 页)")
        print(f"{'='*60}")
        for r in records[:3]:
            print(f"  {json.dumps(r, ensure_ascii=False)}")

        all_results[name] = records

    print(f"\n\n{'='*60}")
    print("  汇总")
    print(f"{'='*60}")
    for name, records in all_results.items():
        print(f"  {name:20s}: {len(records):3d} 条")

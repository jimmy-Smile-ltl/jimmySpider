"""
Example: pubmed_ncbi — PubMed article detail spider.

This example demonstrates the detail half of the pubmed pair:
- Concurrent detail-page fetching with curl_cffi (impersonate="chrome120") to
  bypass TLS-fingerprint checks, with the "checking your browser" interstitial
  handled by retry.
- Structured parsing of authors/affiliations, abstract sections, DOI (triple
  fallback), grants, and copyright from HTML.
- The shared concurrent update engine (HandleMongoDB.update_batch_in_bulk_loop)
  with Redis checkpointing (last_id) and cookie refresh on consecutive
  failures.

PubMed 详情页爬虫 — 从 MongoDB 读取列表页入库的文章，并发抓取详情页并更新字段。
支持 Redis 断点恢复（last_id），依赖 HandleMongoDB.update_batch_in_bulk_loop 的并发引擎。
"""
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from jimmyspider.cache import Cache
from jimmyspider.mongo import HandleMongoDB
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_doi_id, get_cookies_by_url

BASE_URL = "https://pubmed.ncbi.nlm.nih.gov"

HEADERS_DETAIL = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "referer": BASE_URL + "/",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
}

IMPERSONATE = "chrome120"
MAX_WORKERS = 8
BATCH_SIZE = 50
PAGE_SIZE = 500


def _fetch_detail(url: str, cookies: dict = None, timeout: int = 30):
    """使用 curl_cffi 同步请求详情页"""
    for attempt in range(3):
        try:
            resp = curl_requests.get(
                url,
                headers=HEADERS_DETAIL,
                cookies=cookies,
                impersonate=IMPERSONATE,
                timeout=timeout,
            )
            text_lower = resp.text.lower()
            if "checking your browser" in text_lower:
                continue
            if resp.status_code == 200:
                return resp
        except Exception:
            pass
    return None


# ============================================================
# 详情页 HTML 解析
# ============================================================

def _extract_authors_with_affiliations(soup):
    authors = []
    affiliations = []

    authors_list = soup.find("div", class_="authors-list")
    if authors_list:
        for author_span in authors_list.find_all("span", class_="authors-list-item"):
            name_tag = author_span.find("a", class_="full-name")
            if not name_tag:
                name_tag = author_span.find("span", class_="full-name")
            name = name_tag.get_text(strip=True) if name_tag else ""

            aff_ids = []
            aff_titles = []
            for aff_link in author_span.find_all("a", class_="affiliation-link"):
                aff_ids.append(aff_link.get_text(strip=True))
                aff_titles.append(aff_link.get("title", ""))

            authors.append({
                "name": name,
                "affiliation_ids": aff_ids,
                "affiliation_titles": aff_titles,
            })

    affiliations_div = soup.find("div", class_="affiliations")
    if affiliations_div:
        for li in affiliations_div.find_all("li"):
            key_tag = li.find("sup", class_="key")
            key = key_tag.get_text(strip=True) if key_tag else ""
            affiliations.append({"id": key, "text": li.get_text(strip=True)})

    return authors, affiliations


def _extract_abstract(soup):
    abstract_div = soup.find("div", class_="abstract", id="abstract")
    if not abstract_div:
        return None

    result = {"sections": [], "full_text": "", "keywords": ""}

    content_div = abstract_div.find("div", class_="abstract-content selected")
    if content_div:
        for p in content_div.find_all("p"):
            sub = p.find("strong", class_="sub-title")
            if sub:
                section_name = sub.get_text(strip=True).rstrip(":")
                sub.decompose()
                result["sections"].append({
                    "heading": section_name,
                    "text": p.get_text(strip=True),
                })
        result["full_text"] = content_div.get_text(" ", strip=True)

    for p in abstract_div.find_all("p"):
        strong = p.find("strong", class_="sub-title")
        if strong and "keyword" in strong.get_text(strip=True).lower():
            result["keywords"] = p.get_text(strip=True).replace(
                strong.get_text(strip=True), ""
            ).strip()

    return result


def parse_detail_html(html_text: str):
    """从详情页 HTML 提取信息，返回待更新字段的 dict"""
    soup = BeautifulSoup(html_text, "html.parser")
    result = {}

    title_tag = soup.find("h1", class_="heading-title")
    if title_tag:
        result["title"] = title_tag.get_text(strip=True)

    authors, affiliations = _extract_authors_with_affiliations(soup)
    result["authors"] = authors
    result["affiliations"] = affiliations

    pmid_tag = soup.find("strong", class_="current-id")
    if pmid_tag:
        result["pmid"] = pmid_tag.get_text(strip=True)

    # DOI — 三重备选
    doi = None
    doi_tag = soup.find("span", class_="identifier doi")
    if doi_tag:
        doi_link = doi_tag.find("a", class_="id-link")
        if doi_link:
            doi = doi_link.get_text(strip=True)
    if not doi:
        meta_doi = soup.find("meta", {"name": "citation_doi"})
        if meta_doi:
            doi = meta_doi.get("content", "")
    if not doi:
        doi_citation = soup.find("span", class_="citation-doi")
        if doi_citation:
            doi = doi_citation.get_text(strip=True).replace("doi:", "").strip().rstrip(".")
    result["doi"] = doi or ""

    article_source = soup.find("div", class_="article-source")
    if article_source:
        journal_btn = article_source.find("button", class_="journal-actions-trigger")
        if journal_btn:
            result["journal"] = journal_btn.get("title", "") or journal_btn.get_text(strip=True)
        cit_tag = article_source.find("span", class_="cit")
        if cit_tag:
            result["pub_date"] = cit_tag.get_text(strip=True)

    ahead_tag = soup.find("span", class_="ahead-of-print")
    if ahead_tag:
        result["publication_status"] = ahead_tag.get_text(strip=True)

    result["abstract"] = _extract_abstract(soup)

    coi_div = soup.find("div", class_="conflict-of-interest")
    if coi_div:
        stmt = coi_div.find("div", class_="statement")
        if stmt:
            result["conflict_of_interest"] = stmt.get_text(" ", strip=True)

    grants_div = soup.find("div", id="grants")
    if grants_div:
        grant_list = []
        gl = grants_div.find("div", class_="grants-list")
        if gl:
            for gi in gl.find_all("li", class_="grant-item"):
                a_tag = gi.find("a")
                if a_tag:
                    grant_list.append({
                        "grant_id": a_tag.get_text(strip=True),
                        "title": a_tag.get("title", ""),
                    })
        result["grants_and_funding"] = grant_list

    cp = soup.find("p", class_="copyright")
    if cp:
        result["copyright"] = cp.get_text(strip=True)

    return result


# ============================================================
# 爬虫主体
# ============================================================

class SpiderPubMedDetail(JimmySpider):
    def __init__(self, **kwargs):
        kwargs.setdefault("table_name", "pro31_pubmed_ncbi")
        super().__init__(**kwargs)

        # 覆盖基类的 db_manager（确保指向同一个表名）
        self.db_manager = HandleMongoDB(table_name=self.table_name)
        self.last_id_cache = Cache(f"{self.table_name}_detail_last_id")
        self._cookies = None

        self.log_print.print(
            f"详情爬虫初始化完成 table={self.table_name} "
            f"workers={MAX_WORKERS} batch={BATCH_SIZE} page={PAGE_SIZE}"
        )

    def _get_cookies(self, force=False):
        """通过 DrissionPage 获取有效 cookies"""
        if self._cookies and not force:
            return self._cookies
        self.log_print.print("正在通过浏览器获取 cookies ...")
        cookies = get_cookies_by_url(
            BASE_URL + "/",
            click_checkbox=False,
            wait_sec=5,
            headless=False,
        )
        self._cookies = cookies
        self.log_print.print(f"获取到 {len(cookies)} 个 cookies")
        return cookies

    def checkpoint_callback(self, last_id: str):
        """每批写入后，保存最后处理的 _id 到 Redis"""
        self.last_id_cache.record_string(last_id)

    def flush_state(self, _doc=None):
        """连续批量失败时调用，刷新 cookies"""
        self.log_print.warning("连续批量失败，刷新 cookies ...")
        self._cookies = None
        self._get_cookies(force=True)

    # ── 单文档处理回调 ──────────────────────────────────────────────────
    def handle_one_doc(self, doc: dict):
        """并发工作线程调用：获取详情页 → 解析 → 返回更新字段"""
        detail_url = doc.get("detail_url", "")
        if not detail_url:
            return {
                "_id": doc["_id"],
                "detail_parsed": True,
                "detail_error": "missing_url",
            }

        resp = _fetch_detail(detail_url, cookies=self._cookies)
        if not resp:
            # 返回 None 让 update_batch_in_bulk_loop 记入失败计数
            return None

        try:
            parsed = parse_detail_html(resp.text)
        except Exception as e:
            self.log_print.error(f"解析失败 {detail_url}: {e}")
            return {
                "_id": doc["_id"],
                "detail_parsed": True,
                "detail_error": f"parse_error: {e}",
            }

        doi = parsed.get("doi", "")
        parsed["_id"] = doc["_id"]
        parsed["detail_parsed"] = True
        parsed["detail_source_url"] = detail_url
        parsed["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if doi:
            parsed["_doi_id"] = generate_doi_id(doi)

        return parsed

    # ── 主流程 ───────────────────────────────────────────────────────────
    def run(self):
        # 获取初始 cookies
        self._get_cookies()

        filter_condition = {
            "detail_url": {"$exists": True, "$nin": [None, ""]},
            "detail_parsed": {"$ne": True},
        }

        resume_id = self.last_id_cache.get_string(default="")

        self.log_print.print(
            f"开始详情采集 resume_from_id={resume_id or '(从头开始)'}"
        )

        total = self.db_manager.update_batch_in_bulk_loop(
            filter=filter_condition,
            update_func=self.handle_one_doc,
            sort_field="_id",
            sort_way=1,
            resume_from_id=resume_id or None,
            checkpoint_callback=self.checkpoint_callback,
            batch_size=BATCH_SIZE,
            max_workers=MAX_WORKERS,
            logger=self.log_print,
            page_size=PAGE_SIZE,
            file_field="detail_url",
            flush_state=self.flush_state,
        )

        if total == 0:
            self.last_id_cache.clear_value()
            self.log_print.print("全部详情页采集完成，进度已清除")
        else:
            self.log_print.print(
                f"本轮更新了 {total} 条记录，再次运行将继续处理剩余文档"
            )


if __name__ == "__main__":
    pro_path = str(Path(__file__).parent)
    spider = SpiderPubMedDetail(pro_path=pro_path)
    spider.run()

"""
Example: pubmed_ncbi — quick 50-item harvest to JSONL (no MongoDB involved).

This example demonstrates a standalone, dependency-light variant of the pubmed
spider: it crawls the first 5 pages of a fixed search (50 items) using curl_cffi
synchronous requests, merges list-page and detail-page data (detail fields take
priority, list-only fields like snippet/journal_citation are kept), then writes
raw JSONL, a cleaned version (detail_url stripped), and a Springer-filtered
subset (DOI prefix 10.1007 / 10.1186 / 10.1038) for follow-up downloads.

爬取 PubMed 搜索条件前 5 页（共 50 条），合并列表页与详情页数据，
写入 JSONL 文件，最后去除 detail_url 生成清洗版。
"""
import json
import time
from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from jimmyspider.tool import generate_doi_id
BASE_URL = "https://pubmed.ncbi.nlm.nih.gov"
SEARCH_URL = "https://pubmed.ncbi.nlm.nih.gov/"
OUTPUT_RAW = "pubmed_500.jsonl"
OUTPUT_CLEAN = "pubmed_500_clean.jsonl"
OUTPUT_SPRINGER = "springer.jsonl"

SPRINGER_DOI_PREFIXES = ("10.1007", "10.1186", "10.1038")

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
}

def _make_detail_headers(list_page_url):
    """详情页请求头——必须带 referer，否则 PubMed 返回精简版 HTML（无作者机构信息）"""
    return {
        **HEADERS,
        "sec-fetch-site": "same-origin",
        "referer": list_page_url,
    }

PARAMS = {
    "term": "(all[sb]) AND (Clinical Prediction Guides/Broad[filter])",
    "filter": ["other.excludepreprints", "years.2026-2027"],
    "sort": "date",
}

# ============================================================
# 列表页解析
# ============================================================

def parse_list_page(html_text):
    """从搜索结果列表页提取文章基本信息"""
    soup = BeautifulSoup(html_text, "html.parser")
    articles = []

    for article_tag in soup.find_all("article", class_="full-docsum"):
        item = {}

        title_tag = article_tag.find("a", class_="docsum-title")
        if title_tag:
            item["title_list"] = title_tag.get_text(strip=True)
            item["detail_url"] = BASE_URL + title_tag.get("href", "")
            item["article_id"] = title_tag.get("data-article-id", "")

        authors_tag = article_tag.find("span", class_="docsum-authors full-authors")
        if authors_tag:
            item["authors_list"] = [a.strip() for a in authors_tag.get_text(strip=True).split(",") if a.strip()]

        journal_tag = article_tag.find("span", class_="docsum-journal-citation full-journal-citation")
        if journal_tag:
            item["journal_citation"] = journal_tag.get_text(strip=True)

        pmid_tag = article_tag.find("span", class_="docsum-pmid")
        if pmid_tag:
            item["pmid"] = pmid_tag.get_text(strip=True)

        snippet_tag = article_tag.find("div", class_="full-view-snippet")
        if snippet_tag:
            item["snippet"] = snippet_tag.get_text(strip=True)

        articles.append(item)

    return articles


# ============================================================
# 详情页解析
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


def parse_detail_page(html_text):
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
    doi_tag = soup.find("span", class_="identifier doi")
    if doi_tag:
        doi_link = doi_tag.find("a", class_="id-link")
        if doi_link:
            result["doi"] = doi_link.get_text(strip=True)
    if not result.get("doi"):
        meta_doi = soup.find("meta", {"name": "citation_doi"})
        if meta_doi:
            result["doi"] = meta_doi.get("content", "")
    if not result.get("doi"):
        doi_citation = soup.find("span", class_="citation-doi")
        if doi_citation:
            result["doi"] = doi_citation.get_text(strip=True).replace("doi:", "").strip().rstrip(".")
    result["_id"] = generate_doi_id(result.get("doi", ""))
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

    disc = soup.find("p", class_="disclaimer")
    if disc:
        result["disclaimer"] = disc.get_text(strip=True)

    return result


# ============================================================
# 合并列表页与详情页字段
# ============================================================

def merge_article(list_data, detail_data):
    """以详情页数据为主，补充列表页独有的 snippet / journal_citation"""
    merged = {}

    # 详情页字段优先
    merged["title"] = detail_data.get("title") or list_data.get("title_list", "")
    merged["authors"] = detail_data.get("authors") or [
        {"name": n, "affiliation_ids": [], "affiliation_titles": []}
        for n in list_data.get("authors_list", [])
    ]
    merged["affiliations"] = detail_data.get("affiliations", [])
    merged["pmid"] = detail_data.get("pmid") or list_data.get("pmid", "")
    merged["doi"] = detail_data.get("doi", "")
    merged["journal"] = detail_data.get("journal", "")
    merged["pub_date"] = detail_data.get("pub_date", "")
    merged["publication_status"] = detail_data.get("publication_status", "")
    merged["abstract"] = detail_data.get("abstract") or {}
    merged["conflict_of_interest"] = detail_data.get("conflict_of_interest", "")
    merged["grants_and_funding"] = detail_data.get("grants_and_funding", [])
    merged["copyright"] = detail_data.get("copyright", "")

    # 列表页独有字段
    merged["snippet"] = list_data.get("snippet", "")
    merged["journal_citation"] = list_data.get("journal_citation", "")
    merged["article_id"] = list_data.get("article_id", "")
    merged["detail_url"] = list_data.get("detail_url", "")

    return merged


# ============================================================
# 主流程
# ============================================================

def main():
    all_articles = []

    TOTAL_PAGES = 50
    for page in range(1, TOTAL_PAGES + 1):
        print(f"\n{'='*50}")
        print(f"  正在爬取第 {page}/{TOTAL_PAGES} 页（列表页）...")
        print(f"{'='*50}")

        params = {**PARAMS, "page": str(page)}
        resp = curl_requests.get(SEARCH_URL, headers=HEADERS, params=params)
        if resp.status_code != 200:
            print(f"  [错误] 列表页请求失败 HTTP {resp.status_code}")
            continue

        list_page_url = resp.url  # 实际请求的列表页 URL，用作详情页 referer
        list_articles = parse_list_page(resp.text)
        detail_headers = _make_detail_headers(list_page_url)
        print(f"  列表页解析到 {len(list_articles)} 篇文章")

        for idx, la in enumerate(list_articles):
            print(f"    [{page}-{idx+1}] PMID={la.get('pmid')} 请求详情页...", end=" ")
            try:
                detail_resp = curl_requests.get(la["detail_url"], headers=detail_headers)
                if detail_resp.status_code == 200:
                    detail_data = parse_detail_page(detail_resp.text)
                    merged = merge_article(la, detail_data)
                    all_articles.append(merged)
                    print(f"OK -> {merged['title'][:60]}...")
                else:
                    print(f"失败 HTTP {detail_resp.status_code}，使用列表页数据")
                    merged = merge_article(la, {})
                    all_articles.append(merged)
            except Exception as e:
                print(f"异常: {e}，使用列表页数据")
                merged = merge_article(la, {})
                all_articles.append(merged)

            time.sleep(1.5)  # 请求间隔

        time.sleep(2)  # 翻页间隔

    # ---- 写出 JSONL ----
    print(f"\n{'='*50}")
    print(f"  写出 {len(all_articles)} 条记录到 {OUTPUT_RAW}")
    with open(OUTPUT_RAW, "w", encoding="utf-8") as f:
        for article in all_articles:
            f.write(json.dumps(article, ensure_ascii=False) + "\n")
    print(f"  完成: {OUTPUT_RAW}")

    # ---- 清洗: 去除 detail_url ----
    print(f"\n  生成清洗版 {OUTPUT_CLEAN}（去除 detail_url）...")
    with open(OUTPUT_RAW, "r", encoding="utf-8") as f_in, \
         open(OUTPUT_CLEAN, "w", encoding="utf-8") as f_out:
        for line in f_in:
            record = json.loads(line)
            record.pop("detail_url", None)
            f_out.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"  完成: {OUTPUT_CLEAN}")

    # ---- 过滤 Springer 文章 ----
    print(f"\n  过滤 Springer 文章（DOI 前缀: {SPRINGER_DOI_PREFIXES}）...")
    springer_count = 0
    with open(OUTPUT_CLEAN, "r", encoding="utf-8") as f_in, \
         open(OUTPUT_SPRINGER, "w", encoding="utf-8") as f_out:
        for line in f_in:
            record = json.loads(line)
            doi = record.get("doi", "")
            if doi and doi.startswith(SPRINGER_DOI_PREFIXES):
                f_out.write(json.dumps(record, ensure_ascii=False) + "\n")
                springer_count += 1
    print(f"  完成: {OUTPUT_SPRINGER} ({springer_count} 条)")

    print(f"\n  共 {len(all_articles)} 条记录, Springer {springer_count} 条")


if __name__ == "__main__":
    main()

"""
Example: pubmed_ncbi — PubMed search results, list spider (date-range splitting).

This example demonstrates a strategy for search APIs that cap results per query
(PubMed returns at most 10,000 hits / 1,000 pages per search): the search time
range is compressed to a single day, walking backwards day by day from today to
2000-01-01, so no single query ever exceeds the cap. It also shows:
- multi-threading with ThreadPoolExecutor (10 workers) over the pages of one
  date;
- dynamic total-page extraction from HTML attributes (data-pages-amount /
  data-max-page) with graceful fallback to Redis-cached values;
- curl_cffi with the "checking your browser" interstitial handled by retry;
- Redis-backed per-date / per-page completion tracking, per-date article
  counts, and error-page retry.

PubMed 列表页爬虫 — 按天压缩时间范围，多线程爬取搜索结果分页。
- 原因：单个搜索条件最多返回 10,000 条（1,000页），2026年数据超两万条需细化搜索
- 策略：将搜索时间范围压缩到单天，从今天倒序到 2000-01-01，每次减一天
- 动态提取 TOTAL_PAGES（从 HTML 中 data-pages-amount / data-max-page）
- 多线程并发请求页面，Redis 集合追踪成功/失败页
- 支持断点恢复：已完成页跳过，已完成日期跳过，失败页重试
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests

from jimmyspider.cache import Cache
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id

BASE_URL = "https://pubmed.ncbi.nlm.nih.gov"
DEFAULT_TOTAL_PAGES = 50
MAX_WORKERS = 10

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "priority": "u=0, i",
    "referer": "https://pubmed.ncbi.nlm.nih.gov/",
    "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
    "sec-ch-ua-arch": "\"x86\"",
    "sec-ch-ua-bitness": "\"64\"",
    "sec-ch-ua-form-factors": "\"Desktop\"",
    "sec-ch-ua-full-version": "\"147.0.7727.137\"",
    "sec-ch-ua-full-version-list": "\"Google Chrome\";v=\"147.0.7727.137\", \"Not.A/Brand\";v=\"8.0.0.0\", \"Chromium\";v=\"147.0.7727.137\"",
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": "\"\"",
    "sec-ch-ua-platform": "\"Linux\"",
    "sec-ch-ua-platform-version": "\"\"",
    "sec-ch-ua-wow64": "?0",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
}

BASE_SORT = "pubdate"


def generate_date_ranges(start_date_str, end_date_str="2000-01-01"):
    """从 start_date_str 倒序到 end_date_str，每次减一天，生成 "YYYY/MM/DD" 格式日期"""
    start = datetime.strptime(start_date_str, "%Y-%m-%d")
    end = datetime.strptime(end_date_str, "%Y-%m-%d")
    current = start
    while current >= end:
        yield current.strftime("%Y/%m/%d")
        current -= timedelta(days=1)


def build_term(date_str):
    """构建 PubMed term，将时间范围压缩到 date_str 当天"""
    return (
        f'(((all[sb]) AND (Clinical Prediction Guides/Broad[filter])))'
        f' AND ((excludepreprints[Filter]))'
        f' AND (("{date_str}"[Date - Create] : "{date_str}"[Date - Create]))'
    )


# ============================================================
# 动态提取 TOTAL_PAGES
# ============================================================

def extract_total_pages(soup):
    """
    从列表页 HTML 提取最大可浏览页数，多个备选方案依次尝试。
    返回 (total_pages, source) 或 (0, "none") 表示提取失败。
    """
    # 方案1: search-results-chunk 的 data-pages-amount / data-max-page
    chunk = soup.select_one("div.search-results-chunk")
    if chunk:
        pages_amount_str = chunk.get("data-pages-amount", "")
        max_page_str = chunk.get("data-max-page", "")
        if pages_amount_str and max_page_str:
            pages_amount = int(pages_amount_str.replace(",", ""))
            max_page = int(max_page_str)
            return min(pages_amount, max_page), "search-results-chunk"

    # 方案2: label.of-total-pages 文本 "of 23,680"
    label = soup.select_one("label.of-total-pages")
    if label:
        text = label.get_text(strip=True)
        m = re.search(r'of\s+([\d,]+)', text)
        if m:
            pages = int(m.group(1).replace(",", ""))
            return pages, "label.of-total-pages"

    # 方案3: data-last-page 属性
    for btn in soup.select("button.next-page, button.load-button"):
        last_page = btn.get("data-last-page", "")
        if last_page:
            return int(last_page), "data-last-page"

    return 0, "none"


class SpiderPubMedList(JimmySpider):
    def __init__(self, date_str, **kwargs):
        """
        date_str: "YYYY/MM/DD" 格式，如 "2026/05/15"
        """
        kwargs.setdefault("table_name", "pro31_pubmed_ncbi")
        super().__init__(**kwargs)

        self.date_str = date_str
        self.date_display = date_str.replace("/", "-")  # 用于日志和缓存 key

        # 按日期隔离缓存 key
        cache_prefix = f"{self.table_name}_list_{self.date_display}"
        self.pages_done_cache = Cache(f"{cache_prefix}_pages_done")
        self.error_cache = Cache(f"{cache_prefix}_error_pages")
        self.total_pages_cache = Cache(f"{cache_prefix}_total_pages")

        # 全局已完成日期集合（跨条件断点恢复）
        self.completed_dates_cache = Cache(f"{self.table_name}_list_completed_dates")
        # 每天文章数量统计
        self.daily_count_cache = Cache(f"{self.table_name}_list_daily_count")

        self._cookies = {}
        self.total_pages = DEFAULT_TOTAL_PAGES
        self.day_article_count = 0  # 当天累计入库文章数

    # ── 请求参数构建 ───────────────────────────────────────────────────────
    def _build_params(self, page):
        return {
            "term": build_term(self.date_str),
            "sort": BASE_SORT,
            "page": str(page),
        }

    # ── 日期完成状态 ──────────────────────────────────────────────────────
    def _is_date_completed(self):
        return self.completed_dates_cache.is_member_of_set(self.date_display)

    def _mark_date_completed(self):
        self.completed_dates_cache.add_to_set(self.date_display)

    def _record_daily_count(self):
        """将当天文章数量记录到 Redis，key 为日期，value 为文章数"""
        raw = self.daily_count_cache.get_string()
        daily_map = json.loads(raw) if raw else {}
        daily_map[self.date_display] = self.day_article_count
        self.daily_count_cache.record_string(json.dumps(daily_map, ensure_ascii=False))

    def _load_daily_count(self):
        """从 Redis 加载已记录的 daily count"""
        raw = self.daily_count_cache.get_string()
        return json.loads(raw) if raw else {}

    # ── TOTAL_PAGES 管理 ──────────────────────────────────────────────────
    def _load_total_pages(self):
        raw = self.total_pages_cache.get_string()
        if raw:
            try:
                return json.loads(raw).get("total_pages", 0)
            except Exception:
                pass
        return 0

    def _save_total_pages(self):
        self.total_pages_cache.record_string(
            json.dumps({"total_pages": self.total_pages})
        )

    # ── 请求与解析 ───────────────────────────────────────────────────────
    def _fetch_page(self, page: int):
        params = self._build_params(page)
        for attempt in range(10):
            try:
                resp = curl_requests.get(
                    BASE_URL + "/",
                    headers=HEADERS,
                    params=params,
                    cookies=self._cookies,
                )
                if resp.status_code != 200:
                    time.sleep(5)
                    continue

                text_lower = resp.text.lower()
                if "checking your browser" in text_lower:
                    print(f"checking your browser 等待5秒重定向 retry {attempt + 1}  page: {page}")
                    time.sleep(5)
                    continue

                return resp

            except Exception as e:
                print(f"error in fetching page {page} error: {e}")
                time.sleep(5)

        return None

    def _parse_page(self, html_text: str, page: int):
        soup = BeautifulSoup(html_text, "html.parser")
        articles = []

        for article_tag in soup.find_all("article", class_="full-docsum"):
            item = {}

            title_tag = article_tag.find("a", class_="docsum-title")
            if title_tag:
                item["title"] = title_tag.get_text(strip=True)
                item["detail_url"] = BASE_URL + title_tag.get("href", "")
                item["article_id"] = title_tag.get("data-article-id", "")

            authors_tag = article_tag.find("span", class_="docsum-authors full-authors")
            if authors_tag:
                item["authors_list"] = [
                    a.strip()
                    for a in authors_tag.get_text(strip=True).split(",")
                    if a.strip()
                ]

            journal_tag = article_tag.find(
                "span", class_="docsum-journal-citation full-journal-citation"
            )
            if journal_tag:
                item["journal_citation"] = journal_tag.get_text(strip=True)
                doi_match = re.search(r'doi:\s*(10\.\S+)', item["journal_citation"])
                if doi_match:
                    item["doi"] = doi_match.group(1).rstrip('.')

            pmid_tag = article_tag.find("span", class_="docsum-pmid")
            if pmid_tag:
                item["pmid"] = pmid_tag.get_text(strip=True)

            snippet_tag = article_tag.find("div", class_="full-view-snippet")
            if snippet_tag:
                item["snippet"] = snippet_tag.get_text(strip=True)
            if item.get("detail_url"):
                item["_id"] = generate_string_id(item["detail_url"])
            articles.append(item)
        return articles

    # ── 页面完成状态管理 ─────────────────────────────────────────────────
    def _is_page_done(self, page: int):
        return self.pages_done_cache.is_member_of_set(str(page))

    def _mark_page_done(self, page: int):
        self.pages_done_cache.add_to_set(str(page))

    def _mark_page_error(self, page: int):
        self.error_cache.add_to_set(json.dumps({"page": page}))

    def _remove_page_error(self, page: int):
        self.error_cache.remove_from_set(json.dumps({"page": page}))

    def _get_undone_pages(self):
        undone = []
        for page in range(1, self.total_pages + 1):
            if not self._is_page_done(page):
                undone.append(page)
        return undone

    # ── 单页处理（多线程工作函数） ──────────────────────────────────────────
    def _process_one_page(self, page: int):
        """返回 (page, success: bool, article_count: int)"""
        resp = self._fetch_page(page)
        if not resp:
            self._mark_page_error(page)
            return (page, False, 0)

        articles = self._parse_page(resp.text, page)

        if articles:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for a in articles:
                a.setdefault("create_time", now)
                a["date_created"] = self.date_display
            self.save_result(articles)
            self.day_article_count += len(articles)

        self._mark_page_done(page)
        self._remove_page_error(page)
        return (page, True, len(articles))

    # ── 错误页重试 ───────────────────────────────────────────────────────
    def _retry_error_pages(self):
        error_pages = self.error_cache.get_set_members()
        if not error_pages:
            return

        error_page_nums = []
        for raw in error_pages:
            try:
                error_page_nums.append(int(json.loads(raw)["page"]))
            except Exception:
                pass

        if not error_page_nums:
            return

        self.log_print.print(f"  [{self.date_display}] 重试 {len(error_page_nums)} 个错误页 ...")
        for page in error_page_nums:
            page, ok, count = self._process_one_page(page)
            if ok:
                self.log_print.print(f"  [{self.date_display}] 错误页 {page} 重试成功 ({count} 篇)")

        remaining = len(self.error_cache.get_set_members())
        self.log_print.print(f"  [{self.date_display}] 错误页重试完成, 剩余 {remaining} 个")

    def handle_only_one_paper(self, soup, detail_url):
        article = {}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        article.setdefault("create_time", now)
        article["date_created"] = self.date_display
        article["detail_url"] = detail_url
        return article

    # ── 主流程 ───────────────────────────────────────────────────────────
    def run(self):
        # 0) 检查该日期是否已完成
        if self._is_date_completed():
            self.log_print.print(
                f"[{self.date_display}] 已完成，跳过"
            )
            return {"date": self.date_display, "status": "skipped", "total": 0}

        self.log_print.print(
            f"\n{'='*60}\n"
            f"开始处理 [{self.date_display}] term={build_term(self.date_str)}\n"
            f"{'='*60}"
        )

        # 1) 请求第一页，获取 TOTAL_PAGES + 解析数据
        self.log_print.print(f"[{self.date_display}] 请求 page=1 获取 TOTAL_PAGES ...")
        resp = self._fetch_page(1)
        if not resp:
            self.log_print.error(f"[{self.date_display}] 首页请求失败，跳过该日期")
            return {"date": self.date_display, "status": "failed", "total": 0}

        soup = BeautifulSoup(resp.text, "html.parser")

        # 检查是否直接跳转到详情页（当天只有一篇文章）
        if resp.url.find("?") == -1:
            self.log_print.print(
                f"[{self.date_display}] 只有一篇文章，直接跳转到详情页，特殊处理 ... 跳转到url {resp.url}"
            )
            result_one_paper = self.handle_only_one_paper(
                soup=soup, detail_url=resp.url
            )
            self.save_result(result_one_paper)
            self.day_article_count = 1
            self._record_daily_count()
            self._mark_date_completed()
            self.log_print.print(
                f"[{self.date_display}] 当天文章数: {self.day_article_count}"
            )
            return {
                "date": self.date_display,
                "status": "completed",
                "total": 1,
            }

        # 动态提取 TOTAL_PAGES
        extracted, source = extract_total_pages(soup)
        if extracted > 0:
            self.total_pages = extracted
            self.log_print.print(
                f"[{self.date_display}] TOTAL_PAGES 从 {source} 提取: {self.total_pages}"
            )
        else:
            cached = self._load_total_pages()
            if cached > 0:
                self.total_pages = cached
                self.log_print.print(
                    f"[{self.date_display}] TOTAL_PAGES 从 Redis 缓存恢复: {self.total_pages}"
                )
            else:
                # 检查是否当天无结果
                if not soup.select_one("article.full-docsum"):
                    self.log_print.print(
                        f"[{self.date_display}] 当天无搜索结果，标记完成，文章数=0"
                    )
                    self._record_daily_count()
                    self._mark_date_completed()
                    return {
                        "date": self.date_display,
                        "status": "completed",
                        "total": 0,
                    }
                self.log_print.print(
                    f"[{self.date_display}] TOTAL_PAGES 提取失败，使用默认值: {self.total_pages}"
                )
        self._save_total_pages()

        # 检查是否超过 PubMed 1000 页限制
        if self.total_pages > 1000:
            self.log_print.warning(
                f"[{self.date_display}] TOTAL_PAGES={self.total_pages} 超过 1000 页限制，"
                f"实际只能获取前 1000 页（约 {min(self.total_pages, 1000) * 10} 条），"
                f"丢失约 {(self.total_pages - 1000) * 10} 条"
            )
            self.total_pages = 1000

        # 处理第一页数据（如果还没完成）
        if not self._is_page_done(1):
            articles = self._parse_page(resp.text, page=1)
            if articles:
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for a in articles:
                    a.setdefault("create_time", now)
                    a["date_created"] = self.date_display
                self.save_result(articles)
                self.day_article_count += len(articles)
            self._mark_page_done(1)
            self.log_print.print(f"  [{self.date_display}] page=1 入库 {len(articles)} 篇")

        # 2) 重试历史错误页
        self._retry_error_pages()

        # 3) 获取待处理页面列表
        undone = self._get_undone_pages()
        total_remaining = len(undone)
        self.log_print.print(
            f"[{self.date_display}] 待处理 {total_remaining} 页 "
            f"(共 {self.total_pages} 页, 已完成 {self.total_pages - total_remaining})"
        )

        if not undone:
            self.log_print.print(
                f"[{self.date_display}] 全部页面已完成，当天文章数={self.day_article_count}"
            )
            self._record_daily_count()
            self._mark_date_completed()
            self.pages_done_cache.clear_value()
            self.total_pages_cache.clear_value()
            return {"date": self.date_display, "status": "completed", "total": 0}

        # 4) 多线程并发处理剩余页面
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._process_one_page, page): page
                for page in undone
            }
            done_count = 0
            for future in as_completed(futures):
                page = futures[future]
                try:
                    page_num, ok, count = future.result()
                except Exception as e:
                    self.log_print.error(f"  [{self.date_display}] page={page} 线程异常: {e}")
                    self._mark_page_error(page)
                    ok, count = False, 0

                done_count += 1
                status = "OK" if ok else "FAIL"
                self.log_print.print(
                    f"  [{done_count}/{total_remaining}] [{self.date_display}] page={page_num} {status}"
                    f" ({count} 篇) 累计={self.insert_num}"
                )

            # 完成后检查
            remaining_errors = len(self.error_cache.get_set_members())
            if remaining_errors == 0:
                self._record_daily_count()
                self.log_print.print(
                    f"[{self.date_display}] 当天文章数={self.day_article_count}，已记录到 Redis daily_count"
                )
                self.pages_done_cache.clear_value()
                self.total_pages_cache.clear_value()
                self._mark_date_completed()
                self.log_print.print(
                    f"[{self.date_display}] 全部页面采集完成，进度已清除，标记为已完成"
                )
            else:
                self.log_print.warning(
                    f"[{self.date_display}] 还有 {remaining_errors} 个错误页，再次运行将自动重试"
                    f"（当天已入库 {self.day_article_count} 篇，错误修复后可能增加）"
                )

        return {
            "date": self.date_display,
            "status": "completed" if remaining_errors == 0 else "partial",
            "total": self.total_pages,
        }


if __name__ == "__main__":
    pro_path = str(Path(__file__).parent)

    # 从今天开始，倒序到 2000-01-01
    today_str = datetime.now().strftime("%Y-%m-%d")
    date_gen = generate_date_ranges(today_str, "2000-01-01")
    date_list = list(date_gen)

    total_dates = len(date_list)

    # 加载 Redis 中已有的 daily_count
    daily_count_cache = Cache("pro31_pubmed_ncbi_list_daily_count")
    existing_daily = daily_count_cache.get_string()
    existing_daily_map = json.loads(existing_daily) if existing_daily else {}
    existing_total = sum(existing_daily_map.values())

    print(f"日期范围: {today_str} -> 2000-01-01, 共 {total_dates} 天")
    if existing_daily_map:
        print(f"Redis 已有 daily_count 记录: {len(existing_daily_map)} 天, 共 {existing_total} 篇文章")

    results = []
    article_total = 0

    for idx, date_str in enumerate(date_list):
        date_display = date_str.replace("/", "-")

        # 如果该日期已有 daily_count 记录且状态为已完成，直接跳过（快速路径）
        if date_display in existing_daily_map:
            print(
                f"\n{'#'*60}\n"
                f"# [{idx+1}/{total_dates}] 日期: {date_display} 已完成(Redis)，跳过\n"
                f"{'#'*60}"
            )
            results.append({
                "date": date_display,
                "status": "skipped",
                "total": existing_daily_map[date_display],
            })
            article_total += existing_daily_map[date_display]
            continue

        print(
            f"\n{'#'*60}\n"
            f"# [{idx+1}/{total_dates}] 处理日期: {date_display}\n"
            f"{'#'*60}"
        )

        spider = SpiderPubMedList(
            date_str=date_str,
            pro_path=pro_path,
        )
        result = spider.run()
        results.append(result)
        article_total += spider.day_article_count

        # 每隔 100 天打印一次进度汇总
        if (idx + 1) % 100 == 0:
            completed = sum(
                1 for r in results if r["status"] in ("completed", "skipped")
            )
            failed = sum(1 for r in results if r["status"] == "failed")
            partial = sum(1 for r in results if r["status"] == "partial")
            print(
                f"\n--- 进度 [{idx+1}/{total_dates}] "
                f"完成={completed} 失败={failed} 部分={partial} "
                f"累计入库={article_total} ---"
            )

    # 汇总报告
    print(f"\n{'='*60}")
    print(f"全部 {total_dates} 个日期处理完毕")
    completed = [r for r in results if r["status"] == "completed"]
    skipped = [r for r in results if r["status"] == "skipped"]
    partial = [r for r in results if r["status"] == "partial"]
    failed = [r for r in results if r["status"] == "failed"]
    print(f"  已完成: {len(completed)}")
    print(f"  已跳过(之前完成): {len(skipped)}")
    print(f"  部分完成(有错误页): {len(partial)}")
    print(f"  失败: {len(failed)}")
    print(f"  累计入库文章数: {article_total}")

    # 打印 daily_count 汇总
    final_daily = daily_count_cache.get_string()
    final_daily_map = json.loads(final_daily) if final_daily else {}
    if final_daily_map:
        non_zero = {k: v for k, v in final_daily_map.items() if v > 0}
        print(f"  有文章的天数: {len(non_zero)}/{len(final_daily_map)}")
        if non_zero:
            sorted_days = sorted(non_zero.items(), reverse=True)
            print(f"  最近有文章的日期 (top 10):")
            for day, count in sorted_days[:10]:
                print(f"    {day}: {count} 篇")

    if partial:
        print(f"  部分完成的日期: {[r['date'] for r in partial]}")
    if failed:
        print(f"  失败的日期: {[r['date'] for r in failed]}")
    print(f"{'='*60}")

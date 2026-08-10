"""
Example: cuni_cz — Charles University (DSpace) repository, list spider.

Demonstrates:
- University-repository scraping in the classic list + detail two-spider pattern
  (run spider_list.py first to seed MongoDB, then spider_detail.py to enrich).
- SingleRequestHandler for HTTP fetching + ThreadPoolExecutor (5 workers) for
  concurrent pagination within one collection type.
- Redis-backed resume: per-page "done" sets, college/type progress cursor, and
  multi-round retry of previously failed pages.

Note: requires ``college_list_with_types.json`` (curated seed data: colleges and
their document-type URLs) in this directory — not shipped with the example.

Run:  python examples/cuni_cz/spider_list.py
"""
import json
import os
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id
from jimmyspider.datetime_utils import convert_date_robust

class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        pro_path = str(Path(__file__).parent)
        self.pro_path = pro_path
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = self.pro_path
        super(Spider, self).__init__(*args, **kwargs)

        self.base_url = "https://dspace.cuni.cz"
        self.college_list_path = os.path.join(self.pro_path, "college_list_with_types.json")

        self.headers_page = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://dspace.cuni.cz/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\"",
        }

        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.progress_cache = Cache(f"{self.table_name}_progress")
        self.error_set = Cache(f"{self.table_name}_error_pages")
        self._page_done_cache: Optional[Cache] = None
        self._page_done_prefix = f"{self.table_name}_pages_done"

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _encode_cache(value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    @staticmethod
    def _decode_cache(value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  Data loading                                                        #
    # ------------------------------------------------------------------ #

    def load_college_list(self) -> List[Dict]:
        if not os.path.exists(self.college_list_path):
            self.log_print.error(f"college list not found: {self.college_list_path}")
            return []
        with open(self.college_list_path, "r", encoding="utf-8") as file:
            try:
                return json.load(file)
            except Exception as exc:
                self.log_print.error(f"college list decode error: {exc}")
                return []

    # ------------------------------------------------------------------ #
    #  HTTP helpers                                                        #
    # ------------------------------------------------------------------ #

    def _build_page_url(self, type_url: str, page: int) -> str:
        return f"{type_url}?page={page}" if page > 1 else type_url

    def _fetch_single(self, url: str) -> Optional[str]:
        response = self.single_fetcher.fetch(
            url, headers=self.headers_page, method="GET", check_size=False,
        )
        if response and response.status_code == 200:
            response.encoding = response.apparent_encoding
            return response.text
        return None

    @staticmethod
    def _parse_pagination(soup: BeautifulSoup) -> Tuple[int, int]:
        pagination = soup.select_one("ul.pagination")
        if not pagination:
            return 1, 1

        current_tag = pagination.select_one("li.active")
        current_text = current_tag.get_text(strip=True) if current_tag else "1"
        try:
            current_page = int(current_text)
        except Exception:
            current_page = 1

        last_tag = pagination.select_one("li.last-page-link a")
        if last_tag:
            last_text = last_tag.get_text(strip=True)
            if last_text.isdigit():
                return int(last_text), current_page

        nums: List[int] = []
        for tag in pagination.select("li"):
            text = tag.get_text(strip=True)
            if text.isdigit():
                nums.append(int(text))
        total_pages = max(nums) if nums else current_page
        return total_pages, current_page

    # ------------------------------------------------------------------ #
    #  Parsing                                                             #
    # ------------------------------------------------------------------ #

    def _parse_one_item(self, item: BeautifulSoup) -> Dict:
        a_tag = item.select_one("h4.artifact-title a")
        title = a_tag.get_text(strip=True) if a_tag else ""
        detail_url = urljoin(self.base_url, a_tag.get("href")) if a_tag else ""

        defence_status = ""
        status_tag = item.select_one("span.defence-status")
        if status_tag:
            defence_status = status_tag.get_text(strip=True)

        author = ""
        author_tag = item.select_one("span.author")
        if author_tag:
            author = author_tag.get_text(strip=True)

        publisher = ""
        publisher_tag = item.select_one("span.publisher")
        if publisher_tag:
            publisher = publisher_tag.get_text(strip=True)

        year = ""
        year_tag = item.select_one("span.publisher-date span.date")
        if year_tag:
            year = year_tag.get_text(strip=True)

        defence_date = ""
        defence_date_tag = item.select_one("div.artifact-defence-date span.date")
        if defence_date_tag:
            defence_date = defence_date_tag.get_text(strip=True).replace(" ", "")
            defence_date = convert_date_robust(defence_date)

        return {
            "title": title,
            "detail_url": detail_url,
            "author": author,
            "publisher": publisher,
            "year": year,
            "defence_status": defence_status,
            "defence_date": defence_date,
        }

    def parse_list_page(self, html_text: str, college: Dict, type_item: Dict, page: int) -> List[Dict]:
        soup = BeautifulSoup(html_text, "html.parser")
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results: List[Dict] = []

        for item in soup.select("ul.ds-artifact-list > li > div"):
            parsed = self._parse_one_item(item)
            id_seed = parsed.get("detail_url") or parsed.get("title")
            item_id = generate_string_id(id_seed) if id_seed else generate_string_id(str(item))

            results.append({
                "_id": item_id,
                "college_index": college.get("index"),
                "college_name_cz": college.get("name_cz"),
                "college_name_en": college.get("name_en"),
                "college_url": college.get("college_url"),
                "type_name_cz": type_item.get("name_cz"),
                "type_name_en": type_item.get("name_en"),
                "type_url": type_item.get("type_url"),
                "type_count": type_item.get("count"),
                "page": page,
                **parsed,
                "create_time": now_ts,
            })

        return results

    # ------------------------------------------------------------------ #
    #  Progress & error tracking                                           #
    # ------------------------------------------------------------------ #

    def _load_progress(self) -> Tuple[int, int]:
        raw = self.progress_cache.get_string(default="")
        if raw:
            data = json.loads(raw)
            return data.get("college_idx", 0), data.get("type_idx", 0)
        return 0, 0

    def _save_progress(self, college_idx: int, type_idx: int) -> None:
        self.progress_cache.record_string(json.dumps({
            "college_idx": college_idx,
            "type_idx": type_idx,
        }))

    def _record_error(self, college_idx: int, type_idx: int, page: int) -> None:
        self.error_set.add_to_set(self._encode_cache({
            "college_idx": college_idx,
            "type_idx": type_idx,
            "page": page,
        }))

    # ------------------------------------------------------------------ #
    #  Per-page completion tracking (within a type)                        #
    # ------------------------------------------------------------------ #

    def _page_done_key(self, college_idx: int, type_idx: int) -> str:
        return f"{self._page_done_prefix}_{college_idx}_{type_idx}"

    def _ensure_page_done_cache(self, college_idx: int, type_idx: int) -> Cache:
        """Get or create the Cache for tracking done pages of current type."""
        expected_key = self._page_done_key(college_idx, type_idx)
        if self._page_done_cache is None or self._page_done_cache.key != expected_key:
            self._page_done_cache = Cache(expected_key)
        return self._page_done_cache

    def _load_done_pages(self, college_idx: int, type_idx: int) -> set:
        cache = self._ensure_page_done_cache(college_idx, type_idx)
        members = cache.get_set_members()
        return {int(p) for p in members if p.isdigit()}

    def _mark_page_done(self, college_idx: int, type_idx: int, page: int) -> None:
        cache = self._ensure_page_done_cache(college_idx, type_idx)
        cache.add_to_set(str(page))

    def _clear_page_done(self, college_idx: int, type_idx: int) -> None:
        cache = self._ensure_page_done_cache(college_idx, type_idx)
        cache.clear_value()

    # ------------------------------------------------------------------ #
    #  Error retry                                                         #
    # ------------------------------------------------------------------ #

    def _retry_errors(self, college_list: List[Dict]) -> None:
        """Retry previously failed pages before starting new work."""
        for round_num in range(3):
            error_keys = list(self.error_set.get_set_members())
            if not error_keys:
                return

            self.log_print.warning(
                f"retry round {round_num + 1}/3, {len(error_keys)} pending errors"
            )
            for key in error_keys:
                info = self._decode_cache(key)
                ci, ti, page = info["college_idx"], info["type_idx"], info["page"]

                if ci >= len(college_list):
                    self.error_set.remove_from_set(key)
                    continue

                college = college_list[ci]
                type_list = college.get("type_list", [])
                if ti >= len(type_list):
                    self.error_set.remove_from_set(key)
                    continue

                type_url = type_list[ti].get("type_url", "")
                if not type_url:
                    self.error_set.remove_from_set(key)
                    continue

                url = self._build_page_url(type_url, page)
                html_text = self._fetch_single(url)
                if html_text:
                    data = self.parse_list_page(html_text, college, type_list[ti], page)
                    if data:
                        self.save_result(insert_list=data)
                        self.error_set.remove_from_set(key)
                        self.log_print.print(
                            f"  fixed [{ci}:{ti}] page:{page} saved {len(data)}"
                        )

    # ------------------------------------------------------------------ #
    #  Single page task (for thread pool)                                  #
    # ------------------------------------------------------------------ #

    def _fetch_one_page(self, type_url: str, page: int) -> Tuple[int, Optional[str]]:
        """Fetch a single page. Returns (page_num, html_text_or_None)."""
        url = self._build_page_url(type_url, page)
        html_text = self._fetch_single(url)
        return page, html_text

    # ------------------------------------------------------------------ #
    #  Type processing (multi-threaded, per-page done tracking)            #
    # ------------------------------------------------------------------ #

    def _process_type(self, college: Dict, type_item: Dict,
                      college_idx: int, type_idx: int) -> bool:
        """
        Process all pages of one type.
        - Page 1 is fetched first to discover total_pages.
        - Pages 2..N are fetched concurrently, each page marked done on success.
        - On resume, already-done pages are skipped.
        """
        type_url = type_item.get("type_url", "")
        if not type_url:
            self.log_print.warning(f"empty type_url, skip")
            return True

        name_label = f"{college.get('name_cz', '?')} / {type_item.get('name_cz', '?')}"
        self.log_print.print(f"[{college_idx}:{type_idx}] {name_label}")

        # --- load already-done pages (resume support) ---
        done_pages = self._load_done_pages(college_idx, type_idx)

        # --- page 1: discover total pages ---
        if 1 not in done_pages:
            url_p1 = self._build_page_url(type_url, 1)
            html_p1 = self._fetch_single(url_p1)
            if not html_p1:
                self._record_error(college_idx, type_idx, 1)
                self.log_print.print(f"  page 1 request failed, recorded for retry")
                return False

            soup = BeautifulSoup(html_p1, "html.parser")
            total_pages, _ = self._parse_pagination(soup)

            data_p1 = self.parse_list_page(html_p1, college, type_item, 1)
            if data_p1:
                self.save_result(insert_list=data_p1)
            self._mark_page_done(college_idx, type_idx, 1)
            self.log_print.print(f"  page:1/{total_pages} saved {len(data_p1)}")
        else:
            # page 1 already done, still need total_pages to know the range
            url_p1 = self._build_page_url(type_url, 1)
            html_p1 = self._fetch_single(url_p1)
            if not html_p1:
                self.log_print.warning(f"  cannot re-fetch page 1 for pagination, abort")
                return False
            soup = BeautifulSoup(html_p1, "html.parser")
            total_pages, _ = self._parse_pagination(soup)
            self.log_print.print(f"  page:1 already done, total_pages={total_pages}")

        if total_pages <= 1:
            self._clear_page_done(college_idx, type_idx)
            return True

        # --- pages 2..N: multi-threaded, skip done pages ---
        pending = [p for p in range(2, total_pages + 1) if p not in done_pages]
        skipped = (total_pages - 1) - len(pending)

        if not pending:
            self.log_print.print(f"  all {total_pages - 1} pages already done (resume)")
            self._clear_page_done(college_idx, type_idx)
            return True

        self.log_print.print(
            f"  pages 2..{total_pages}: {skipped} skipped, "
            f"{len(pending)} pending with 5 threads"
        )

        ok_count = 0
        fail_count = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_page = {
                executor.submit(self._fetch_one_page, type_url, p): p
                for p in pending
            }
            for future in as_completed(future_to_page):
                p = future_to_page[future]
                try:
                    page_num, html_text = future.result()
                except Exception:
                    self._record_error(college_idx, type_idx, p)
                    fail_count += 1
                    continue

                if html_text is None:
                    self._record_error(college_idx, type_idx, p)
                    fail_count += 1
                    continue

                data = self.parse_list_page(html_text, college, type_item, page_num)
                if data:
                    self.save_result(insert_list=data)
                    self._mark_page_done(college_idx, type_idx, page_num)
                    ok_count += 1
                else:
                    self.log_print.warning(f"  page:{page_num} parse empty")
                    self._mark_page_done(college_idx, type_idx, page_num)

        self.log_print.print(
            f"  done: {ok_count}/{len(pending)} ok"
            + (f", {fail_count} failed (will retry)" if fail_count else "")
        )

        # --- cleanup page-level tracking when type is fully done ---
        remaining_errors = self.error_set.get_set_members()
        has_error_for_this_type = any(
            self._decode_cache(k).get("college_idx") == college_idx
            and self._decode_cache(k).get("type_idx") == type_idx
            for k in remaining_errors
        )
        if not has_error_for_this_type:
            self._clear_page_done(college_idx, type_idx)

        return True

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        college_list = self.load_college_list()
        if not college_list:
            self.log_print.error("college_list empty, abort")
            return

        self._retry_errors(college_list)

        start_ci, start_ti = self._load_progress()
        self.log_print.print(f"resume from college:{start_ci} type:{start_ti}")

        for ci in range(start_ci, len(college_list)):
            college = college_list[ci]
            type_list = college.get("type_list", [])
            if not type_list:
                self._save_progress(ci + 1, 0)
                continue

            t_start = start_ti if ci == start_ci else 0
            for ti in range(t_start, len(type_list)):
                type_item = type_list[ti]
                ok = self._process_type(college, type_item, ci, ti)

                if ok:
                    if ti + 1 < len(type_list):
                        self._save_progress(ci, ti + 1)
                    else:
                        self._save_progress(ci + 1, 0)
                else:
                    self.log_print.warning(f"stopped at [{ci}:{ti}] due to failure")
                    return

                time.sleep(1)

        remaining = len(self.error_set.get_set_members())
        if remaining == 0:
            self.progress_cache.clear_value()
            self.log_print.print("all done, progress cleared")
        else:
            self.log_print.warning(f"{remaining} error pages remain, progress kept for retry")

        self.log_print.print("list run completed")


if __name__ == "__main__":
    spider = Spider()
    spider.run_all()

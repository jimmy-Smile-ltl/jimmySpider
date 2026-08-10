"""
Example: escholarship_org — University of California research portal, list spider.

Demonstrates:
- Scraping a JSON search API (XHR with X-Requested-With: XMLHttpRequest)
  instead of HTML pages, with a page size of 10 enforced by the API.
- Year-range splitting: 10-year buckets before 2000, 2-year buckets
  2000-2009, and 1-year buckets 2010+ — a general strategy to beat search
  APIs that truncate results past a fixed count (here 100,000).
- ThreadPoolExecutor (5 workers) per year range + Redis-backed resume cursor
  and multi-round error retry.

Run:  python examples/escholarship_org/spider_list.py
"""
import json
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        pro_path = str(Path(__file__).parent)
        self.pro_path = pro_path
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = self.pro_path
        super(Spider, self).__init__(*args, **kwargs)

        self.base_url = "https://escholarship.org"
        self.api_url = "https://escholarship.org/api/pageData/search"

        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://escholarship.org/search?type_of_work=dissertation",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\"",
        }

        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.progress_cache = Cache(f"{self.table_name}_progress")
        self.error_set = Cache(f"{self.table_name}_error_pages")

    # ------------------------------------------------------------------ #
    #  Year ranges                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def build_year_ranges() -> List[Tuple[int, int]]:
        """年份 1960-2026，按批次规则生成搜索范围。"""
        ranges: List[Tuple[int, int]] = []
        # 2000年以前 10年一批次  eg 1960-1969
        for start in range(1960, 2000, 10):
            ranges.append((start, start + 9))
        # 2000-2009 2年一批次  eg 2000-2001
        for start in range(2000, 2010, 2):
            ranges.append((start, start + 1))
        # 2010-2026 1年一批次  eg 2010-2010
        for start in range(2010, 2027):
            ranges.append((start, start))
        return ranges

    # ------------------------------------------------------------------ #
    #  HTTP helpers                                                        #
    # ------------------------------------------------------------------ #

    def _fetch_api(self, pub_year_start: int, pub_year_end: int,
                   start: int) -> Optional[Dict]:
        """调用 eScholarship 搜索 API，成功返回 dict，失败返回 None。"""
        params = {
            "type_of_work": "dissertation",
            "pub_year_start": str(pub_year_start),
            "pub_year_end": str(pub_year_end),
            "start": str(start),
        }
        response = self.single_fetcher.fetch(
            self.api_url,
            headers=self.headers,
            params=params,
            method="GET",
            check_size=False,
        )
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception as exc:
                self.log_print.warning(f"JSON decode error: {exc}")
                return None
        return None

    # ------------------------------------------------------------------ #
    #  Parsing                                                             #
    # ------------------------------------------------------------------ #

    def _parse_results(self, data: Dict, year_start: int, year_end: int,
                       page: int) -> List[Dict]:
        """将 API 返回的 JSON 解析为入库条目列表。"""
        search_results = data.get("searchResults", [])
        if not search_results:
            return []

        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows: List[Dict] = []

        for item in search_results:
            result_id = item.get("id", "")
            detail_url = f"{self.base_url}/uc/item/{result_id}" if result_id else ""

            rows.append({
                "_id": generate_string_id(result_id or str(item)),
                "eschol_id": result_id,
                "title": item.get("title", ""),
                "abstract": item.get("abstract", ""),
                "authors": item.get("authors", []),
                "advisors": item.get("advisors", []),
                "pub_year": item.get("pub_year", ""),
                "genre": item.get("genre", ""),
                "peer_reviewed": item.get("peerReviewed", False),
                "unit_info": item.get("unitInfo", {}),
                "supp_files": item.get("supp_files", []),
                "rights": item.get("rights"),
                "content_type": item.get("content_type"),
                "detail_url": detail_url,
                "year_range_start": year_start,
                "year_range_end": year_end,
                "page": page,
                "create_time": now_ts,
            })

        return rows

    # ------------------------------------------------------------------ #
    #  Progress                                                            #
    # ------------------------------------------------------------------ #

    def _load_progress(self) -> int:
        """返回下一个要处理的 year range 索引。"""
        raw = self.progress_cache.get_string(default="")
        if raw:
            try:
                return int(raw)
            except Exception:
                return 0
        return 0

    def _save_progress(self, year_idx: int) -> None:
        self.progress_cache.record_string(str(year_idx))

    @staticmethod
    def _encode_error(year_start: int, year_end: int, page: int) -> str:
        return json.dumps({
            "year_start": year_start,
            "year_end": year_end,
            "page": page,
        }, ensure_ascii=True)

    @staticmethod
    def _decode_error(value: str) -> Optional[Dict]:
        try:
            return json.loads(value)
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    #  Error retry                                                         #
    # ------------------------------------------------------------------ #

    def _retry_errors(self) -> None:
        """重试之前失败的页面，最多 3 轮。"""
        for round_num in range(3):
            error_keys = list(self.error_set.get_set_members())
            if not error_keys:
                return

            self.log_print.warning(
                f"retry round {round_num + 1}/3, {len(error_keys)} pending errors"
            )
            for key in error_keys:
                info = self._decode_error(key)
                if not info:
                    self.error_set.remove_from_set(key)
                    continue

                ys, ye = info["year_start"], info["year_end"]
                page = info["page"]
                start = page * 10

                data = self._fetch_api(ys, ye, start)
                if data and data.get("searchResults"):
                    rows = self._parse_results(data, ys, ye, page)
                    if rows:
                        self.save_result(insert_list=rows)
                        self.error_set.remove_from_set(key)
                        self.log_print.print(
                            f"  fixed [{ys}-{ye}] page:{page} saved {len(rows)}"
                        )
                time.sleep(0.5)

    # ------------------------------------------------------------------ #
    #  Single page fetch (for thread pool)                                 #
    # ------------------------------------------------------------------ #

    def _fetch_one_page(self, year_start: int, year_end: int,
                        page: int) -> Tuple[int, Optional[Dict]]:
        start = page * 10
        data = self._fetch_api(year_start, year_end, start)
        return page, data

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        year_ranges = self.build_year_ranges()
        self.log_print.print(
            f"year ranges: {len(year_ranges)} batches, "
            f"first {year_ranges[0]}, last {year_ranges[-1]}"
        )

        self._retry_errors()

        start_idx = self._load_progress()
        self.log_print.print(f"resume from year_range index: {start_idx}")

        for yi in range(start_idx, len(year_ranges)):
            ys, ye = year_ranges[yi]
            label = f"[{yi}/{len(year_ranges)}] {ys}-{ye}"
            self.log_print.print(label)

            # --- page 0: discover total count ---
            data_p0 = self._fetch_api(ys, ye, 0)
            if not data_p0:
                self.error_set.add_to_set(self._encode_error(ys, ye, 0))
                self.log_print.warning(f"  page 0 request failed, recorded for retry")
                continue

            total_count = int(data_p0.get("count", 0))
            total_pages = (total_count + 9) // 10  # ceil division by 10
            self.log_print.print(f"  count:{total_count} pages:{total_pages}")

            # save page 0 results
            rows_p0 = self._parse_results(data_p0, ys, ye, 0)
            if rows_p0:
                self.save_result(insert_list=rows_p0)

            if total_pages <= 1:
                self._save_progress(yi + 1)
                continue

            # --- pages 1..N-1: multi-threaded ---
            pending = list(range(1, total_pages))
            self.log_print.print(f"  pages 1..{total_pages - 1}: {len(pending)} pending with 5 threads")

            ok_count = 0
            fail_count = 0
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_page = {
                    executor.submit(self._fetch_one_page, ys, ye, p): p
                    for p in pending
                }
                for future in as_completed(future_to_page):
                    p = future_to_page[future]
                    try:
                        page_num, data = future.result()
                    except Exception:
                        self.error_set.add_to_set(self._encode_error(ys, ye, p))
                        fail_count += 1
                        continue

                    if data is None:
                        self.error_set.add_to_set(self._encode_error(ys, ye, p))
                        fail_count += 1
                        continue

                    rows = self._parse_results(data, ys, ye, page_num)
                    if rows:
                        self.save_result(insert_list=rows)
                        ok_count += 1
                    else:
                        self.log_print.warning(f"  page:{page_num} parse empty")

            self.log_print.print(
                f"  done: {ok_count}/{len(pending)} ok"
                + (f", {fail_count} failed" if fail_count else "")
            )

            self._save_progress(yi + 1)
            time.sleep(1)

        # --- cleanup ---
        remaining = len(self.error_set.get_set_members())
        if remaining == 0:
            self.progress_cache.clear_value()
            self.log_print.print("all done, progress cleared")
        else:
            self.log_print.warning(
                f"{remaining} error pages remain, progress kept for retry"
            )

        self.log_print.print("list run completed")


if __name__ == "__main__":
    spider = Spider()
    spider.run_all()

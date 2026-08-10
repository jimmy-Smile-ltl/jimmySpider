"""
Example: escholarship_org — University of California research portal, detail spider.

Demonstrates:
- Extracting structured JSON embedded in a page's <script> tag
  (window.jscholApp_initialPageData) instead of parsing visible HTML — a
  common pattern for SPA / Next.js-style sites.
- Using the shared concurrent update engine
  (HandleMongoDB.update_batch_in_bulk_loop) with in-process retry of failed
  requests and Redis checkpointing (last_id).
- AWS WAF note: eScholarship sits behind AWS WAF; production runs obtained a
  session cookie (aws-waf-token) from a real browser. Cookies are deliberately
  NOT hardcoded here — acquire them at runtime and inject into self.cookies.

Run:  python examples/escholarship_org/spider_detail.py
"""
import datetime
import json
import re
import time
from pathlib import Path
from typing import Dict, Optional

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.mongo import HandleMongoDB
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider


class SpiderDetail(JimmySpider):
    BASE_URL = "https://escholarship.org"

    # ------------------------------------------------------------------ #
    #  Parsers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_detail_json(page_data: Dict, source_url: str = "") -> Dict:
        result = {}

        result["eschol_id"] = page_data.get("id")
        result["title"] = page_data.get("title")
        result["genre"] = page_data.get("genre")
        result["published"] = page_data.get("published")
        result["source"] = page_data.get("source")
        result["status"] = page_data.get("status")

        header = page_data.get("header", {})
        result["campus_id"] = header.get("campusID", "")
        result["campus_name"] = header.get("campusName", "")

        result["authors"] = page_data.get("authors", [])
        result["advisors"] = page_data.get("advisors", [])

        pdf_rel = page_data.get("pdf_url", "")
        result["pdf_url"] = f"https://escholarship.org{pdf_rel}" if pdf_rel else ""

        result["appears_in"] = page_data.get("appearsIn", [])
        result["unit"] = page_data.get("unit", {})

        attrs = page_data.get("attrs", {})
        result["abstract"] = attrs.get("abstract", "")
        result["keywords"] = attrs.get("keywords", [])
        result["subjects"] = attrs.get("subjects", [])
        result["language"] = attrs.get("language", "")
        result["is_peer_reviewed"] = attrs.get("is_peer_reviewed", False)
        result["content_length"] = attrs.get("content_length", 0)

        if result["abstract"]:
            abs_soup = BeautifulSoup(result["abstract"], "html.parser")
            result["abstract_text"] = abs_soup.get_text(separator="\n", strip=True)

        result["local_ids"] = attrs.get("local_ids", [])
        result["citation"] = page_data.get("citation", {})
        result["usage"] = page_data.get("usage", [])

        sidebar = page_data.get("sidebar", [])
        related = []
        for block in sidebar:
            if block.get("kind") == "RecentArticles":
                related = block.get("attrs", {}).get("items", [])
        result["related_items"] = related

        result["source_url"] = source_url
        return result

    @classmethod
    def parse_detail_html(cls, html_text: str, source_url: str = "") -> Dict:
        soup = BeautifulSoup(html_text, "html.parser")

        for s in soup.find_all("script"):
            if s.string and "jscholApp_initialPageData" in s.string:
                m = re.search(
                    r"window\.jscholApp_initialPageData\s*=\s*(\{.*\})\s*;?\s*$",
                    s.string, re.DOTALL,
                )
                if m:
                    try:
                        page_data = json.loads(m.group(1))
                        return cls.parse_detail_json(page_data, source_url)
                    except json.JSONDecodeError:
                        pass
                break

        return {"source_url": source_url, "_parse_error": True}

    # ------------------------------------------------------------------ #
    #  Init                                                               #
    # ------------------------------------------------------------------ #

    def __init__(self, *args, **kwargs):
        pro_path = str(Path(__file__).parent)
        self.pro_path = pro_path
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = self.pro_path
        super(SpiderDetail, self).__init__(*args, **kwargs)

        self.db_manager = HandleMongoDB(table_name=self.table_name)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.detail_url_field = "detail_url"

        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://escholarship.org/uc/item/qt3d14t6zs",
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
        # Session cookies are obtained at runtime from a real browser (AWS WAF
        # challenge) — do not hardcode tokens in source.
        self.cookies = {}
        self.batch_size = int(kwargs.get("batch_size", 100))
        self.max_workers = int(kwargs.get("max_workers", 5))
        self.page_size = int(kwargs.get("page_size", 1000))

        self.last_id_cache = Cache(f"{self.table_name}_detail_last_id")

    # ------------------------------------------------------------------ #
    #  Fetch + update_func                                                #
    # ------------------------------------------------------------------ #

    def fetch_detail(self, url: str):
        response = self.single_fetcher.fetch(
            url,
            headers=self.headers,
            method="GET",
            cookies=self.cookies,
            check_size=False,
        )
        if response:
            response.encoding = response.apparent_encoding
        return response

    def handle_one_doc(self, doc: Dict) -> Optional[Dict]:
        detail_url = doc.get(self.detail_url_field)
        file_id = doc["_id"]
        if not detail_url:
            self.log_print.warning(f"_id={file_id} 缺少 {self.detail_url_field}，跳过")
            return None

        try:
            for retry in range(1,6):
                response = self.fetch_detail(detail_url)
                if not response or response.status_code != 200:
                    self.log_print.warning(f"_id={file_id} 请求失败 url={detail_url}  retry={retry}")
                    time.sleep(3)
                    continue
            else:
                return None

            parsed = self.parse_detail_html(response.text, source_url=detail_url)
            html_path = self.html_saver.save_html(html=response.text, file_id=file_id)
            parsed["_id"] = file_id
            parsed["html_path"] = html_path
            parsed["detail_source_url"] = detail_url
            parsed["update_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return parsed
        except Exception as e:
            self.log_print.error(f"_id={file_id} 解析详情页异常 url={detail_url} err={e}")
            return None

    # ------------------------------------------------------------------ #
    #  Checkpoint + state                                                 #
    # ------------------------------------------------------------------ #

    def checkpoint_callback(self, id_str: str) -> None:
        self.last_id_cache.record_string(id_str)

    def flush_state(self) -> None:
        if getattr(self.single_fetcher, "proxyUtil", None):
            self.single_fetcher._refresh_proxy()
            self.log_print.warning("连续失败，触发代理刷新")

    # ------------------------------------------------------------------ #
    #  Main entry                                                         #
    # ------------------------------------------------------------------ #

    def run_all(self):
        filter_condition = {
            self.detail_url_field: {"$exists": True, "$nin": [None, ""]},
            "html_path": {"$exists": False},
        }

        resume_id = self.last_id_cache.get_string(default="")
        self.log_print.print(
            f"escholarship 详情爬取启动 batch_size={self.batch_size} "
            f"max_workers={self.max_workers} resume_from_id={resume_id!r}"
        )

        self.db_manager.update_batch_in_bulk_loop(
            filter=filter_condition,
            update_func=self.handle_one_doc,
            sort_field="_id",
            sort_way=1,
            resume_from_id=resume_id or None,
            checkpoint_callback=self.checkpoint_callback,
            batch_size=self.batch_size,
            max_workers=self.max_workers,
            logger=self.log_print,
            page_size=self.page_size,
            file_field=self.detail_url_field,
            flush_state=self.flush_state,
        )

        self.log_print.print("escholarship 详情爬取完成")


if __name__ == "__main__":
    spider = SpiderDetail()
    spider.run_all()

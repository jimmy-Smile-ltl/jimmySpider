"""
Example: cuni_cz — Charles University (DSpace) repository, detail spider.

Demonstrates:
- The detail half of a list + detail pair: reads documents seeded by
  spider_list.py from MongoDB, fetches each detail page (with ?show=full),
  parses DC meta tags / item-page fields / file lists, saves the raw HTML, and
  writes the enriched document back.
- Driven by HandleMongoDB.update_batch_in_bulk_loop — the shared concurrent
  update engine (batch fetch + ThreadPoolExecutor + Redis checkpoint via
  last_id + proxy refresh on consecutive failures).

Run:  python examples/cuni_cz/spider_detail.py
"""
import datetime
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.mongo import HandleMongoDB
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider


class SpiderDetail(JimmySpider):
    BASE_URL = "https://dspace.cuni.cz"

    # ------------------------------------------------------------------ #
    #  Parsers                                                            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_meta_tags(soup) -> Dict:
        raw = defaultdict(list)
        for tag in soup.find_all("meta", attrs={"name": True, "content": True}):
            name = tag["name"].strip()
            content = tag["content"].strip()
            if content:
                raw[name].append(content)
        return {name: (vals[0] if len(vals) == 1 else vals)
                for name, vals in raw.items()}

    @staticmethod
    def meta_first(meta: Dict, *names: str) -> str:
        for name in names:
            val = meta.get(name, "")
            if isinstance(val, list):
                val = val[0] if val else ""
            if val:
                return val
        return ""

    @staticmethod
    def parse_item_page_fields(soup) -> Dict:
        raw = defaultdict(list)
        for wrapper in soup.select(".item-page-field-wrapper"):
            heading = wrapper.select_one("h4.item-view-heading") or wrapper.select_one("h4")
            if not heading:
                continue
            key = heading.get_text(strip=True).rstrip(":")
            heading_text = heading.get_text(strip=True)
            for div in wrapper.select("div"):
                text = div.get_text(strip=True)
                # 排除仅包含 heading 文本的 div
                if text and text != key and text != heading_text:
                    raw[key].append(text)
            if not raw[key]:
                for span in wrapper.select("span"):
                    text = span.get_text(strip=True)
                    if text:
                        raw[key].append(text)
            if not raw[key]:
                texts = [t.strip() for t in wrapper.stripped_strings
                         if t.strip() not in (key, heading_text)]
                seen = set()
                uniq = []
                for t in texts:
                    if t not in seen:
                        seen.add(t)
                        uniq.append(t)
                raw[key] = uniq
        return {k: (v[0] if len(v) == 1 else v) for k, v in raw.items()}

    @staticmethod
    def parse_full_view_fields(soup) -> Dict:
        raw = defaultdict(list)
        for tr in soup.select("table.ds-includeSet-table > tr"):
            tds = tr.select("td")
            if len(tds) < 2:
                continue
            key = tds[0].get_text(strip=True).rstrip(":")
            value = tds[1].get_text(strip=True)
            if value:
                raw[key].append(value)
        return {k: (v[0] if len(v) == 1 else v) for k, v in raw.items()}

    @classmethod
    def parse_files_simple(cls, soup) -> List[Dict]:
        files = []
        for entry in soup.select("h5.item-list-entry a"):
            text = entry.get_text(strip=True)
            href = entry.get("href", "")
            url = urljoin(cls.BASE_URL, href) if href else ""
            m = re.match(r"(.+?)\s*\(([^)]+)\)$", text)
            file_name = m.group(1).strip() if m else text
            file_size = m.group(2).strip() if m else ""
            files.append({
                "file_name": file_name,
                "file_size": file_size,
                "file_url": url,
            })
        return files

    @classmethod
    def parse_files_full(cls, soup) -> List[Dict]:
        files = []
        for fw in soup.select("div.file-list > div.file-wrapper"):
            dts = fw.select("dl dt")
            dds = fw.select("dl dd")
            info = {}
            for dt, dd in zip(dts, dds):
                key = dt.get_text(strip=True).rstrip(":")
                val = dd.get_text(strip=True)
                info[key] = val
            link = fw.select_one("div.file-link a")
            if link:
                info["file_url"] = urljoin(cls.BASE_URL, link.get("href", ""))
            files.append(info)
        return files

    @classmethod
    def parse_detail_html(cls, html_text: str, source_url: str = "") -> Dict:
        soup = BeautifulSoup(html_text, "html.parser")
        result = {}

        meta = cls.parse_meta_tags(soup)
        result["meta"] = meta
        result["dc_title"] = cls.meta_first(meta, "DC.title", "citation_title")
        result["dc_creator"] = cls.meta_first(meta, "DC.creator")
        result["dc_contributor"] = cls.meta_first(meta, "DC.contributor")
        result["dc_language"] = cls.meta_first(meta, "DC.language", "citation_language")
        result["dc_identifier"] = cls.meta_first(meta, "DC.identifier")
        result["dc_type"] = cls.meta_first(meta, "DC.type")
        result["dc_date_accepted"] = cls.meta_first(meta, "DCTERMS.dateAccepted")
        result["dc_date_available"] = cls.meta_first(meta, "DCTERMS.available")
        result["citation_title"] = cls.meta_first(meta, "citation_title")
        result["citation_author"] = cls.meta_first(meta, "citation_author")
        result["citation_date"] = cls.meta_first(meta, "citation_date")
        result["citation_pdf_url"] = cls.meta_first(meta, "citation_pdf_url")
        result["citation_keywords"] = cls.meta_first(meta, "citation_keywords")
        result["citation_abstract_url"] = cls.meta_first(meta, "citation_abstract_html_url")

        h2 = soup.select_one("h2.page-header")
        result["page_title"] = h2.get_text(strip=True) if h2 else result["dc_title"]

        permalink_tag = soup.select_one(".simple-item-view-uri a")
        result["permanent_link"] = permalink_tag.get_text(strip=True) if permalink_tag else ""

        result["simple_view_fields"] = cls.parse_item_page_fields(soup)

        files = cls.parse_files_full(soup)
        if not files:
            files = cls.parse_files_simple(soup)
        result["files"] = files

        full_fields = cls.parse_full_view_fields(soup)
        if full_fields:
            result["full_view_fields"] = full_fields
        return result

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
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }

        self.batch_size = int(kwargs.get("batch_size", 100))
        self.max_workers = int(kwargs.get("max_workers", 10))
        self.page_size = int(kwargs.get("page_size", 1000))

        self.last_id_cache = Cache(f"{self.table_name}_detail_last_id")

    # ------------------------------------------------------------------ #
    #  Fetch + update_func                                                #
    # ------------------------------------------------------------------ #

    def fetch_detail(self, url: str):
        response = self.single_fetcher.fetch(
            url,
            headers=self.headers,
            params={"show": "full"},
            method="GET",
            check_size=False,
        )
        if response:
            response.encoding = response.apparent_encoding
        return response

    def handle_one_doc(self, doc: Dict) -> Optional[Dict]:
        detail_url = doc.get(self.detail_url_field)
        file_id = doc["_id"]
        if not detail_url:
            self.log_print.warning(
                f"_id={file_id} 缺少 {self.detail_url_field}，跳过"
            )
            return None

        try:
            response = self.fetch_detail(detail_url)
            if not response or response.status_code != 200:
                self.log_print.warning(
                    f"_id={file_id} 请求失败 url={detail_url}"
                )
                return None

            parsed = self.parse_detail_html(response.text, source_url=detail_url)
            html_path = self.html_saver.save_html(html=response.text, file_id=file_id)
            parsed["_id"] = file_id
            parsed["html_path"] = html_path
            parsed["update_time"] = datetime.datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            return parsed
        except Exception as e:
            self.log_print.error(
                f"_id={file_id} 解析详情页异常 url={detail_url} err={e}"
            )
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
            f"cuni_cz 详情爬取启动 batch_size={self.batch_size} "
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

        self.log_print.print("cuni_cz 详情爬取完成")


if __name__ == "__main__":
    spider = SpiderDetail()
    spider.run_all()

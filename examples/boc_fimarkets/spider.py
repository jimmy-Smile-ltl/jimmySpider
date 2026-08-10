"""
boc_fimarkets/spider.py
────────────────────────
示例：中国银行金融市场宏观研究 (https://www.boc.cn/fimarkets/summarize/) 抓取 ——
银行官网 list+detail 两阶段采集。

演示内容：
  1. list+detail 两阶段：列表页 div.news ul.list li 提取标题/日期/详情链接，
     详情页 div.sub_con 提取正文与附件链接
  2. 分页 URL 规律：index.html → index_1.html → index_2.html …
     （page<=1 时用首页 index.html，其余页 index_{page-1}.html）
  3. 编码自适应：response.apparent_encoding 处理 GBK/UTF-8 混合编码页面
  4. 日期标准化：列表页 [YYYY-MM-DD] 文本交给 HandleDatetime.convert_date_robust
     智能解析（框架 datetime_utils 模块）
  5. 正文递归清洗：extractSoup.extract_content_recursively 提取 div.sub_con 纯文本
  6. 附件拆分入库：一个详情页的每个附件（PDF/Word）单独成记录，
     _id = MD5(detail_url::file_url)；无附件时正文单条入库
  7. 三级错误重试：列表失败页、详情失败页各自独立 Set（error_page_set /
     error_detail_set），主流程后各重试 3 轮

数据字段：标题、发布时间、url、正文内容、file_url、file_title、file_type。
"""

import json
import time
import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.datetime_utils import convert_date_robust
from jimmyspider.soup import extractSoup
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        pro_path = str(Path(__file__).parent)
        self.pro_path = pro_path
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = self.pro_path
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.extract_soup = extractSoup()

        self.base_url = "https://www.boc.cn"
        self.index_base = "https://www.boc.cn/fimarkets/summarize/"

        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": self.index_base,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }
        # 原实现携带站点级 Cookie（ariaappid / ariauseGraymode），
        # 非登录凭证，留空即可正常请求
        self.cookies = {}

        self.log_page = Cache(f"{self.table_name}_log_page")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")
        self.error_detail_set = Cache(f"{self.table_name}_error_detail_set")

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  List pages                                                          #
    # ------------------------------------------------------------------ #

    def build_list_url(self, page: int) -> str:
        if page <= 1:
            return urljoin(self.index_base, "index.html")
        return urljoin(self.index_base, f"index_{page - 1}.html")

    def get_list_page(self, page: int) -> Optional[str]:
        url = self.build_list_url(page)
        response = self.single_fetcher.fetch(
            url,
            headers=self.headers,
            cookies=self.cookies,
            method="GET",
            check_size=False,
        )
        if response and response.status_code == 200:
            response.encoding = response.apparent_encoding
            return response.text
        return None

    def parse_list_page(self, html_text: str) -> List[Dict]:
        soup = BeautifulSoup(html_text, "html.parser")
        results = []
        for li in soup.select("div.news ul.list li"):
            link_tag = li.select_one("a")
            if not link_tag:
                continue
            href = link_tag.attrs.get("href", "")
            detail_url = urljoin(self.index_base, href) if href else ""
            title = link_tag.get_text(strip=True)

            date_tag = li.select_one("span")
            date_str = date_tag.get_text(strip=True) if date_tag else ""
            date_str = date_str.strip("[]").strip()
            publish_time = convert_date_robust(date_str) if date_str else ""

            results.append({
                "标题": title,
                "发布时间": publish_time,
                "url": detail_url,
            })
        return results

    # ------------------------------------------------------------------ #
    #  Detail pages                                                        #
    # ------------------------------------------------------------------ #

    def get_detail_page(self, detail_url: str) -> Optional[str]:
        response = self.single_fetcher.fetch(
            detail_url,
            headers=self.headers,
            cookies=self.cookies,
            method="GET",
            check_size=False,
        )
        if response and response.status_code == 200:
            response.encoding = response.apparent_encoding
            return response.text
        return None

    def parse_detail_page(self, html_text: str, detail_url: str) -> Dict:
        soup = BeautifulSoup(html_text, "html.parser")
        content_div = soup.select_one("div.sub_con")
        content_text = self.extract_soup.extract_content_recursively(content_div) if content_div else ""

        file_items = []
        if content_div:
            for a_tag in content_div.select("a[href]"):
                href = a_tag.attrs.get("href", "")
                if not href:
                    continue
                file_url = urljoin(detail_url, href)
                file_title = a_tag.get_text(strip=True)
                file_items.append({
                    "file_url": file_url,
                    "file_title": file_title,
                })

        if not file_items:
            file_items = [{"file_url": "", "file_title": ""}]

        return {
            "content": content_text,
            "file_items": file_items,
        }

    # ------------------------------------------------------------------ #
    #  Retry error pages/details                                            #
    # ------------------------------------------------------------------ #

    def handle_error_page(self) -> bool:
        error_keys = list(self.error_page_set.get_set_members())
        if not error_keys:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True

        for error_key in error_keys:
            page_info = self._decode_cache(error_key)
            page = page_info.get("page")
            html_text = self.get_list_page(page)
            if html_text:
                detail_list = self.parse_list_page(html_text)
                if detail_list:
                    self.handle_detail_list(detail_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(f"handle_error_page page:{page} 采集失败")
                return False

        return len(self.error_page_set.get_set_members()) == 0

    def handle_error_detail(self) -> bool:
        error_keys = list(self.error_detail_set.get_set_members())
        if not error_keys:
            self.log_print.print("handle_error_detail: 无 detail 需要处理")
            return True

        for error_key in error_keys:
            detail_info = self._decode_cache(error_key)
            detail_url = detail_info.get("url")
            if not detail_url:
                self.error_detail_set.remove_from_set(error_key)
                continue

            html_text = self.get_detail_page(detail_url)
            if html_text:
                self.save_detail_records(detail_info, html_text)
                self.error_detail_set.remove_from_set(error_key)
            else:
                self.log_print.print(f"handle_error_detail url:{detail_url} 采集失败")
                return False

        return len(self.error_detail_set.get_set_members()) == 0

    # ------------------------------------------------------------------ #
    #  Save records                                                        #
    # ------------------------------------------------------------------ #

    def save_detail_records(self, base_info: Dict, html_text: str) -> int:
        detail_url = base_info.get("url", "")
        detail_data = self.parse_detail_page(html_text, detail_url)
        content_text = detail_data.get("content", "")
        file_items = detail_data.get("file_items", [])

        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_list = []
        for item in file_items:
            file_url = item.get("file_url", "")
            file_title = item.get("file_title", "")
            file_type = ""
            if file_url and "." in file_url:
                file_type = file_url.rsplit(".", 1)[-1].lower()

            unique_key = f"{detail_url}::{file_url}" if file_url else detail_url
            data_list.append({
                "_id": generate_string_id(unique_key),
                "标题": base_info.get("标题", ""),
                "发布时间": base_info.get("发布时间", ""),
                "url": detail_url,
                "正文内容": content_text,
                "file_url": file_url,
                "file_title": file_title,
                "file_type": file_type,
                "create_time": now_ts,
            })

        if data_list:
            self.save_result(insert_list=data_list)
        return len(data_list)

    def handle_detail_list(self, detail_list: List[Dict]) -> None:
        for detail in detail_list:
            detail_url = detail.get("url")
            if not detail_url:
                continue
            html_text = self.get_detail_page(detail_url)
            if html_text:
                self.save_detail_records(detail, html_text)
            else:
                self.error_detail_set.add_to_set(self._encode_cache(detail))

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        progress_str = self.log_page.get_string(default="")
        if progress_str:
            progress = json.loads(progress_str)
            start_page = progress.get("page", 1)
        else:
            start_page = 1

        self.log_print.print(
            f"开始抓取 中国银行金融市场 宏观经济研究, 恢复自 page:{start_page}..."
        )

        page = start_page
        empty_pages = 0

        while True:
            html_text = self.get_list_page(page)
            if html_text:
                detail_list = self.parse_list_page(html_text)
                if detail_list:
                    self.handle_detail_list(detail_list)
                    self.log_print.print(f"  page:{page} 采集成功 {len(detail_list)} 条")
                    empty_pages = 0
                else:
                    self.log_print.warning(f"  page:{page} 解析无数据")
                    empty_pages += 1

                self.log_page.record_string(json.dumps({"page": page + 1}))

                if empty_pages >= 1:
                    self.log_print.print("  已无更多数据")
                    break
                page += 1
                time.sleep(1)
            else:
                page_info = {"page": page}
                self.log_print.print(f"  page:{page} 列表请求失败，记录错误页")
                self.error_page_set.add_to_set(self._encode_cache(page_info))
                break

        self.log_print.print("主流程采集完成")
        self.log_page.clear_value()

        for retry in range(3):
            self.log_print.warning(f"开始处理错误 page (第 {retry + 1} 次)")
            if self.handle_error_page():
                break

        for retry in range(3):
            self.log_print.warning(f"开始处理错误 detail (第 {retry + 1} 次)")
            if self.handle_error_detail():
                break


if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_all()

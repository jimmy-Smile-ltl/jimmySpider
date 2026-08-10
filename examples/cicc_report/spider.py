"""
cicc_report/spider.py
──────────────────────
示例：中金公司研报列表 (https://www.cicc.com/business/list_214_223_{page}.html) 抓取。

演示内容：
  1. 加速乐 (JSL) CDN cookie 挑战的自动化处理流程：
     a. 首次请求返回含 document.cookie 赋值表达式的挑战页，
        用 execjs 求值得到 __jsl_clearance_s
     b. 部分加密变体返回 go({...}) 数据，需调用还原出的 JS 函数
        计算 clearance cookie（见 _load_cookie_js_ctx）
     c. 携带 cookie 重试，直到拿到真实列表页
  2. 分页 URL 模板 + 总页数解析（div.jump data-page-max / 页码文本）
  3. Redis 断点续爬（log_page）+ 错误页重试（error_page_set）

注意：
  - 还原出的 cookie 计算脚本（main_兼容不同hash.js）属于 JS 逆向研究
    产物，未随本示例发布；如需完整解挑战，请将还原脚本放到本目录并
    保持文件名一致（或改写 _load_cookie_js_ctx 指向自己的脚本）。
  - 依赖 pyexecjs（pip install pyexecjs）。
"""

import os
import re
import json
import time
import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import execjs
from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = Path(__file__).parent
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://www.cicc.com"
        self.list_url_tpl = "https://www.cicc.com/business/list_214_223_{page}.html"

        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://www.cicc.com/business/list_214_223_1.html",
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

        self.log_page = Cache(f"{self.table_name}_log_page")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")
        self._cookies: Dict[str, str] = {}
        self._cookie_js_ctx = self._load_cookie_js_ctx()

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  Cookie / JS challenge helpers                                       #
    # ------------------------------------------------------------------ #

    def _load_cookie_js_ctx(self) -> Optional[execjs.ExternalRuntime.Context]:
        js_path = os.path.join(Path(__file__).parent, "main_兼容不同hash.js")
        if not os.path.exists(js_path):
            self.log_print.error(f"Cookie JS file not found: {js_path}")
            return None
        with open(js_path, "r", encoding="utf-8") as file:
            js_text = file.read()
        try:
            return execjs.compile(js_text)
        except Exception as exc:
            self.log_print.error(f"Cookie JS compile error: {exc}")
            return None

    @staticmethod
    def _extract_cookie_expr(html_text: str) -> Optional[str]:
        match = re.search(r"document\.cookie\s*=\s*(.+?)\s*location", html_text, re.DOTALL)
        return match.group(1) if match else None

    @staticmethod
    def _cookie_str_to_dict(cookie_str: str) -> Dict[str, str]:
        result = {}
        for part in cookie_str.split("; "):
            if "=" in part:
                key, value = part.split("=", 1)
                result[key] = value
            else:
                result[part] = ""
        return result

    def _eval_cookie_expr(self, cookie_expr: str) -> Optional[str]:
        js_code = f"""
function getCookieValue() {{
    return {cookie_expr};
}}
getCookieValue();
"""
        try:
            ctx = execjs.compile(js_code)
            return ctx.call("getCookieValue")
        except Exception as exc:
            self.log_print.error(f"Cookie expr eval error: {exc}")
            return None

    @staticmethod
    def _extract_go_data(html_text: str) -> Optional[Dict]:
        match = re.search(r"go\(({.*?})\)</script>", html_text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except Exception:
            return None

    def _compute_clearance(self, go_data: Dict) -> Optional[str]:
        if not self._cookie_js_ctx:
            return None
        try:
            return self._cookie_js_ctx.call("get_cookie", go_data)
        except Exception as exc:
            self.log_print.error(f"Cookie JS compute error: {exc}")
            return None

    @staticmethod
    def _is_challenge(html_text: str) -> bool:
        return "document.cookie" in html_text or "go(" in html_text

    def _fetch_html(self, url: str, cookies: Optional[Dict[str, str]] = None) -> Optional[str]:
        response = self.single_fetcher.fetch(
            url,
            headers=self.headers,
            cookies=cookies or {},
            method="GET",
            check_size=False,
            check_status_code=False,
            stream=False
        )
        if response and response.status_code == 200:
            return response.text
        return None

    def _solve_challenge(self, url: str) -> Optional[str]:
        response = self.single_fetcher.fetch(
            url,
            headers=self.headers,
            cookies={},
            method="GET",
            check_size=False,
            check_status_code=False,
            stream=False
        )
        cookies = response.cookies.get_dict()
        html_text = response.text
        cookie_expr = self._extract_cookie_expr(html_text)
        if cookie_expr:
            cookie_value = self._eval_cookie_expr(cookie_expr)
            if cookie_value:
                cookie_dict = self._cookie_str_to_dict(cookie_value)
                if cookie_dict.get("__jsl_clearance_s"):
                    cookies["__jsl_clearance_s"] = cookie_dict["__jsl_clearance_s"]

            response = self.single_fetcher.fetch(
                url,
                headers=self.headers,
                cookies=cookies,
                method="GET",
                check_size=False,
                check_status_code=False,
                stream=False
            )
            html_text = response.text

        go_data = self._extract_go_data(html_text)
        if go_data:
            clearance = self._compute_clearance(go_data)
            if clearance:
                cookies["__jsl_clearance_s"] = clearance
                response = self.single_fetcher.fetch(
                    url,
                    headers=self.headers,
                    cookies=cookies,
                    method="GET",
                    check_size=False,
                    check_status_code=True,
                    stream=False
                )
                if not response or response.status_code != 200:
                    return None
                html_text = response.text

        self._cookies = cookies
        return html_text

    # ------------------------------------------------------------------ #
    #  Step 1: Fetch list page                                              #
    # ------------------------------------------------------------------ #

    def get_list_page(self, page: int) -> Optional[str]:
        url = self.list_url_tpl.format(page=page)
        if self._cookies:
            html_text = self._fetch_html(url, cookies=self._cookies)
            if html_text and not self._is_challenge(html_text):
                return html_text

        return self._solve_challenge(url)

    # ------------------------------------------------------------------ #
    #  Step 2: Parse list page                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_total_page(soup: BeautifulSoup) -> int:
        input_tag = soup.select_one("div.jump > input")
        if input_tag:
            value = input_tag.attrs.get("data-page-max")
            if value and str(value).isdigit():
                return int(value)

        span = soup.select_one("div.jump > span")
        if span:
            match = re.search(r"(\d+)/(\d+)", span.get_text(" ", strip=True))
            if match:
                return int(match.group(2))
        return 0

    def parse_list_page(self, html_text: str) -> Tuple[List[Dict], int]:
        soup = BeautifulSoup(html_text, "html.parser")
        total_page = self._parse_total_page(soup)
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        results = []
        for item in soup.select("div.ui-article-list > div.item"):
            title_tag = item.select_one("div.title > a")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            href = title_tag.attrs.get("href", "")
            file_url = urljoin(self.base_url, href) if href else ""
            publish_time = ""
            time_tag = item.select_one("p.time")
            if time_tag:
                publish_time = time_tag.get_text(strip=True)

            results.append({
                "_id": generate_string_id(file_url or title),
                "标题": title,
                "发布时间": publish_time,
                "url": file_url,
                "file_url": file_url,
                "file_type": "pdf" if file_url.lower().endswith(".pdf") else "",
                "create_time": now_ts,
            })

        return results, total_page

    # ------------------------------------------------------------------ #
    #  Step 3: Retry error pages                                            #
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
                data_list, _ = self.parse_list_page(html_text)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(f"handle_error_page page:{page} 采集失败")
                return False

        return len(self.error_page_set.get_set_members()) == 0

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
            f"开始抓取 CICC 研报列表, 恢复自 page:{start_page}..."
        )

        page = start_page
        total_page = start_page + 1

        while True:
            html_text = self.get_list_page(page)
            if html_text:
                data_list, total_page_temp = self.parse_list_page(html_text)
                if total_page_temp:
                    total_page = total_page_temp
                if data_list:
                    self.save_result(insert_list=data_list)
                    self.log_print.print(
                        f"  page:{page}/{total_page} 采集成功 {len(data_list)} 条"
                    )
                else:
                    self.log_print.warning(
                        f"  page:{page}/{total_page} 解析无数据"
                    )

                self.log_page.record_string(json.dumps({"page": page + 1}))

                if total_page is not None and page >= total_page:
                    self.log_print.print("  已采集完毕")
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


if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_all()

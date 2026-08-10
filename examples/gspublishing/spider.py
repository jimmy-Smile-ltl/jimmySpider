"""
gspublishing/spider.py
──────────────────────
示例：高盛全球研报检索 (https://www.gspublishing.com/research) 列表抓取 ——
国际投行研报平台 + 复杂 JSON POST API。

演示内容：
  1. POST JSON API：advanced-search 接口接收复杂 JSON payload
     （facets / language / sort / limitTo / filter 查询语法），
     响应 JSON 的 documents 数组为研报记录、pagination.pageCount 为总页数
  2. 毫秒时间戳格式化：publicationDateTime / lastPublishedDateTime /
     lastModifiedDateTime 统一转 YYYY-MM-DD HH:MM:SS
  3. 附件链接构建：path / downloadPath 相对路径用 urljoin 拼绝对 URL，
     并按扩展名提取 file_type
  4. raw_data 全量保留：除映射字段外，原始记录一并入库，便于后续扩展字段
  5. JSON 序列化断点 {page} + 错误页重试（最多 3 轮）

数据字段：标题、概述、发布时间、最后发布时间、最后修改时间、url、
file_url、file_type、来源(sources)、作者(authors)、媒体(media)。
"""

import json
import time
import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

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
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://www.gspublishing.com"
        self.list_api_url = "https://www.gspublishing.com/research/search/reports/advanced-search"

        self.headers = {
            "accept": "application/prs.gir-search-service.v2+json;charset=UTF-8",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "content-type": "application/json;charset=UTF-8",
            "csrf-token": "undefined",
            "origin": "https://www.gspublishing.com",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.gspublishing.com/content/research/site/search.html?facets=()&language=%5B%22en%22%2C%22ja%22%2C%22zh%22%5D&page=1&sort=time&limitTo=%5B%22%22%5D&filter=(%20totalPages%20IN%20%5B1%2C400%5D)",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        }
        # 站点无需登录，留空即可
        self.cookies = {}

        self.log_page = Cache(f"{self.table_name}_log_page")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  Step 1: Fetch list page                                              #
    # ------------------------------------------------------------------ #

    def get_list_page(self, page: int) -> Optional[Dict]:
        payload = {
            "facets": "()",
            "language": "[\"en\",\"ja\",\"zh\"]",
            "page": page,
            "sort": "time",
            "limitTo": "[\"\"]",
            "filter": "( totalPages IN [1,400])",
            "applyHighlighting": True,
        }

        response = self.single_fetcher.fetch(
            self.list_api_url,
            headers=self.headers,
            cookies=self.cookies,
            data=json.dumps(payload, separators=(",", ":")),
            method="POST",
            check_size=False,
        )
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception as exc:
                self.log_print.error(f"JSON decode error: {exc}")
        return None

    # ------------------------------------------------------------------ #
    #  Step 2: Parse records                                                #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _format_ts(ts_ms: Optional[int]) -> str:
        if not ts_ms:
            return ""
        try:
            return datetime.datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""

    def parse_records(self, records: List[Dict]) -> List[Dict]:
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results = []
        for record in records:
            path = record.get("path", "")
            url = urljoin(self.base_url, path) if path else ""
            download_path = record.get("downloadPath")
            file_url = urljoin(self.base_url, download_path) if download_path else ""
            file_type = ""
            if file_url and "." in file_url:
                file_type = file_url.rsplit(".", 1)[-1].lower()

            results.append({
                "_id": generate_string_id(record.get("id") or url),
                "标题": record.get("title", ""),
                "概述": record.get("synopsis", ""),
                "发布时间": self._format_ts(record.get("publicationDateTime")),
                "最后发布时间": self._format_ts(record.get("lastPublishedDateTime")),
                "最后修改时间": self._format_ts(record.get("lastModifiedDateTime")),
                "url": url,
                "file_url": file_url,
                "file_type": file_type,
                "来源": record.get("sources", []),
                "作者": record.get("authors", []),
                "媒体": record.get("media", []),
                "create_time": now_ts,
                "raw_data": record,
            })
        return results

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
            res_json = self.get_list_page(page)
            if res_json:
                data_list = self.parse_records(res_json.get("documents", []))
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
            f"开始抓取 gspublishing 研报列表, 恢复自 page:{start_page}..."
        )

        page = start_page
        total_page = None

        while True:
            res_json = self.get_list_page(page)
            if res_json:
                data_list = self.parse_records(res_json.get("documents", []))
                pagination = res_json.get("pagination", {})
                total_page = pagination.get("pageCount") or total_page or 1

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

"""
示例：中国货币网信用评级公告抓取 (chinamoney)

抓取中国货币网（www.chinamoney.com.cn）的信用评级公告：
- 数据接口：/ags/ms/cm-u-notice-issue/ratingAnNotice（POST form 表单），
  按「年份 × 页码」分片请求，返回 JSON（每页 30 条）
- 字段标准化：标题 / 发表机构 / 发布时间 / 详情页 url / PDF 下载链接 file_url
- Redis 断点续爬：log_year_page 记录年份+页码断点，error_page_set 记录失败页并重试
"""

import datetime
import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        # 自动定位项目目录（等价于入口处传 pro_path=Path(__file__).parent）
        kwargs.setdefault("pro_path", Path(__file__).parent)
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://www.chinamoney.com.cn"
        self.list_api_url = "https://www.chinamoney.com.cn/ags/ms/cm-u-notice-issue/ratingAnNotice"
        self.file_down_url = "https://www.chinamoney.com.cn/dqs/cm-s-notice-query/fileDownLoad.do"

        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.chinamoney.com.cn",
            "Pragma": "no-cache",
            "Referer": "https://www.chinamoney.com.cn/chinese/zxpjbgh/?bondSrno=&tabtabNum=1&tabid=0&bnc=&ro=&sdt=&edt=",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }
        self.cookies = {}

        # 年份范围：2004 ~ 当前年
        self.start_year = 2004
        self.end_year = datetime.datetime.now().year

        self.log_year_page = Cache(f"{self.table_name}_log_year_page")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  Step 1: Fetch one page for a given year                             #
    # ------------------------------------------------------------------ #

    def get_list_page(self, year: int, page: int) -> Optional[Dict]:
        """
        POST /ratingAnNotice
        固定参数: channelId=2564, drftClAngl=11, scnd=1104, pageSize=30
        按年份切分时间范围，逐页抓取。
        """
        data = {
            "channelId": "2564",
            "bondSrno": "",
            "drftClAngl": "11",
            "scnd": "1104",
            "ratingOrg": "",
            "bondNameCode": "",
            "pageNo": str(page),
            "pageSize": "30",
            "startDate": f"{year}-01-01",
            "endDate": f"{year + 1}-01-01",
            "limit": "1",
            "timeln": "1",
        }
        response = self.single_fetcher.fetch(
            self.list_api_url,
            headers=self.headers,
            cookies=self.cookies,
            data=data,
            method="POST",
            check_size=False,
        )
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                self.log_print.error(f"JSON decode error: {e}")
        return None

    # ------------------------------------------------------------------ #
    #  Step 2: Parse records                                               #
    # ------------------------------------------------------------------ #

    def parse_records(self, records: List[Dict]) -> List[Dict]:
        """
        将原始 record 转为统一字段格式。

        字段对齐：
            _id       — detail_url 生成的唯一 ID
            标题       — 报告名称（title）
            发表机构   — 评级机构（prefix）
            发布时间   — releaseDate
            url       — 详情页 HTML 链接（draftPath 拼接 base_url）
            file_url   — PDF 下载链接（fileDownLoad + contentId）
            file_type   — suffix（pdf / word 等）
            概述       — 保留空字符串（列表页无摘要）
            create_time — 抓取时间
        """
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        results = []
        for record in records:
            content_id = record.get("contentId", "")
            draft_path = record.get("draftPath", "")
            detail_url = urljoin(self.base_url, draft_path) if draft_path else ""
            file_url = (
                f"{self.file_down_url}?mode=open&contentId={content_id}&priority=0"
                if content_id
                else ""
            )
            results.append({
                "_id": generate_string_id(detail_url or content_id),
                "标题": record.get("title", ""),
                "发表机构": record.get("prefix", ""),
                "发布时间": record.get("releaseDate", ""),
                "url": detail_url,
                "file_url": file_url,
                "file_type": record.get("suffix", ""),
                "概述": "",
                "create_time": now_ts,
            })
        return results

    # ------------------------------------------------------------------ #
    #  Step 3: Handle one year                                             #
    # ------------------------------------------------------------------ #

    def handle_one_year(self, year: int, start_page: int = 1) -> None:
        """逐页抓取某一年的所有评级报告数据。"""
        page = start_page

        while True:
            res_json = self.get_list_page(year, page)

            if res_json and res_json.get("head", {}).get("rep_code") == "200":
                page_data = res_json.get("data", {})
                total_page = page_data.get("pageTotalSize", 1)
                records = res_json.get("records", [])

                data_list = self.parse_records(records)
                if data_list:
                    self.save_result(insert_list=data_list)
                    self.log_print.print(
                        f"  [{year}] page:{page}/{total_page} 采集成功 {len(data_list)} 条"
                    )
                else:
                    self.log_print.warning(
                        f"  [{year}] page:{page}/{total_page} 解析无数据"
                    )

                # 保存进度
                self.log_year_page.record_string(
                    json.dumps({"year": year, "page": page + 1})
                )

                if page >= total_page:
                    self.log_print.print(f"  [{year}] 已采集完毕")
                    break
                page += 1
                time.sleep(1)

            else:
                page_info = {"year": year, "page": page}
                self.log_print.print(
                    f"  [{year}] page:{page} 请求失败，记录错误页"
                )
                self.error_page_set.add_to_set(self._encode_cache(page_info))
                break   # 单页失败跳出，避免死循环；由 handle_error_page 补采

    # ------------------------------------------------------------------ #
    #  Step 4: Retry error pages                                           #
    # ------------------------------------------------------------------ #

    def handle_error_page(self) -> bool:
        """重试所有失败页。"""
        error_keys = list(self.error_page_set.get_set_members())
        if not error_keys:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True

        for error_key in error_keys:
            page_info = self._decode_cache(error_key)
            year = page_info.get("year")
            page = page_info.get("page")

            res_json = self.get_list_page(year, page)
            if res_json and res_json.get("head", {}).get("rep_code") == "200":
                records = res_json.get("records", [])
                data_list = self.parse_records(records)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(
                    f"handle_error_page year:{year} page:{page} 采集失败"
                )
                return False

        return len(self.error_page_set.get_set_members()) == 0

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        # ---------- load / resume progress ----------
        progress_str = self.log_year_page.get_string(default="")
        if progress_str:
            progress = json.loads(progress_str)
            start_year = progress.get("year", self.start_year)
            start_page = progress.get("page", 1)
        else:
            start_year = self.start_year
            start_page = 1

        self.log_print.print(
            f"开始抓取 chinamoney 信用评级报告, "
            f"恢复自 year:{start_year}, page:{start_page}..."
        )

        # ---------- iterate years ----------
        for year in range(start_year, self.end_year + 1):
            page = start_page if year == start_year else 1
            self.log_print.print(f"开始抓取年份: {year}")
            self.handle_one_year(year, start_page=page)

        self.log_print.print("主流程采集完成")
        self.log_year_page.clear_value()

        # ---------- retry error pages ----------
        for retry in range(3):
            self.log_print.warning(f"开始处理错误 page (第 {retry + 1} 次)")
            if self.handle_error_page():
                break


if "__main__" == __name__:
    spider = Spider()
    spider.run_all()

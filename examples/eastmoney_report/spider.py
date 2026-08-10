"""
示例：东方财富研报数据抓取 (eastmoney_report)

演示内容：
- SingleRequestHandler 同步请求，覆盖 GET/POST、headers/cookies/params/data 传参
- 研报多分类（个股/行业/新股/宏观/策略/券商晨报）分页抓取
- Redis 断点续爬：按分类记录页码，失败页入 Set 后统一自动重试
- 研报详情链接与 PDF 链接的构建规则
"""

import datetime
import json
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        if "test_url" not in kwargs:
            kwargs["test_url"] = "https://data.eastmoney.com/report/"
        super(Spider, self).__init__(*args, **kwargs)
        self.test_url = "https://data.eastmoney.com/report/"
        self.list2_url = "https://reportapi.eastmoney.com/report/list2"
        self.list_url = "https://reportapi.eastmoney.com/report/list"
        self.new_stock_url = "https://reportapi.eastmoney.com/report/newStockList"
        self.jg_url = "https://reportapi.eastmoney.com/report/jg"
        self.begin_time = "2010-01-01"
        self.end_time = datetime.datetime.now().strftime("%Y-%m-%d")
        self.single_handler = SingleRequestHandler(test_url=self.test_url)
        self.log_category = Cache(f"{self.table_name}_log_category")
        self.log_page = Cache(f"{self.table_name}_log_page")
        self.log_category_finished = Cache(f"{self.table_name}_log_category_finished")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")
        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Origin": "https://data.eastmoney.com",
            "Referer": "https://data.eastmoney.com/report/stock.jshtml",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-site",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Chromium\";v=\"147\", \"Not/A)Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\"",
        }
        self.cookies = {
            "qgqp_b_id": "eae15102c3316da97349d75b35c97f07",
            "fullscreengg": "1",
            "fullscreengg2": "1",
            "st_si": "03346776275771",
            "st_asi": "delete",
            "st_pvi": "92999383062685",
            "st_sp": "2023-04-27%2000%3A30%3A56",
            "st_inirUrl": "https%3A%2F%2Fcn.bing.com%2F",
            "st_sn": "22",
            "st_psi": "2025062311202898-113300303756-1044950580",
        }

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    def get_rating_change_str(self, rating_change: Optional[str]) -> str:
        mapping = {
            "0": "调高",
            "1": "调低",
            "2": "首次",
            "3": "维持",
            "4": "无",
        }
        return mapping.get(str(rating_change), "-")

    def build_report_link(self, item: Dict, channel: str) -> str:
        info_code = item.get("infoCode")
        encode_url = item.get("encodeUrl")
        if channel in {"个股研报", "新股研报"} and info_code:
            return f"https://data.eastmoney.com/report/info/{info_code}.html"
        if channel == "行业研报" and info_code:
            return f"https://data.eastmoney.com/report/zw_industry.jshtml?infocode={info_code}"
        if channel == "宏观研报" and encode_url:
            return f"https://data.eastmoney.com/report/zw_macresearch.jshtml?encodeUrl={encode_url}"
        if channel == "策略报告" and encode_url:
            return f"https://data.eastmoney.com/report/zw_strategy.jshtml?encodeUrl={encode_url}"
        if channel == "券商晨报" and encode_url:
            return f"https://data.eastmoney.com/report/zw_brokerreport.jshtml?encodeUrl={encode_url}"
        return ""

    def build_pdf_link(self, item: Dict) -> str:
        info_code = item.get("infoCode")
        if not info_code:
            return ""
        return f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"

    def extract_report_list(self, res_json: Dict, channel: str) -> List[Dict]:
        if not res_json or "data" not in res_json:
            return []
        report_list = []
        for item in res_json.get("data", []):
            report_link = self.build_report_link(item, channel)
            report_pdf = self.build_pdf_link(item)
            info_code = item.get("infoCode")
            now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            report = {
                "股票代码": item.get("stockCode"),
                "股票简称": item.get("stockName"),
                "行业名称": item.get("industryName") or item.get("indvInduName"),
                "报告名称": item.get("title"),
                "报告链接": report_link,
                "报告pdf": report_pdf,
                "东财评级": item.get("emRatingName"),
                "评级变动": self.get_rating_change_str(item.get("ratingChange")),
                "机构名称": item.get("orgSName"),
                "日期": item.get("publishDate"),
                "作者": item.get("author"),
                "报告类型": channel,
                "infoCode": info_code,
                "create_time": now_ts,
                "update_time": now_ts,
                "raw_data": item,
            }
            report["_id"] = info_code or generate_string_id(report_link)
            report_list.append(report)
        return report_list

    def get_stock_report_list(self, page: int) -> Optional[Dict]:
        data = {
            "beginTime": self.begin_time,
            "endTime": self.end_time,
            "industryCode": "*",
            "ratingChange": None,
            "rating": None,
            "orgCode": None,
            "code": "*",
            "rcode": "",
            "pageSize": 50,
            "p": page,
            "pageNo": page,
            "pageNum": page,
            "pageNumber": page,
        }
        response = self.single_handler.fetch(
            self.list2_url,
            headers=self.headers,
            cookies=self.cookies,
            data=json.dumps(data, ensure_ascii=False),
            method="POST",
        )
        return response.json() if response else None

    def get_industry_report_list(self, page: int) -> Optional[Dict]:
        params = {
            "cb": "",
            "industryCode": "*",
            "pageSize": "50",
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": self.begin_time,
            "endTime": self.end_time,
            "pageNo": str(page),
            "fields": "",
            "qType": "1",
            "orgCode": "",
            "rcode": "",
            "p": str(page),
            "pageNum": str(page),
            "pageNumber": str(page),
            "_": str(int(datetime.datetime.now().timestamp() * 1000)),
        }
        response = self.single_handler.fetch(
            self.list_url,
            headers=self.headers,
            cookies=self.cookies,
            params=params,
            method="GET",
        )
        return response.json() if response else None

    def get_new_stock_report_list(self, page: int) -> Optional[Dict]:
        params = {
            "cb": "",
            "pageSize": "50",
            "beginTime": self.begin_time,
            "endTime": self.end_time,
            "pageNo": str(page),
            "fields": "",
            "qType": "4",
            "p": str(page),
            "pageNum": str(page),
            "pageNumber": str(page),
            "_": str(int(datetime.datetime.now().timestamp() * 1000)),
        }
        response = self.single_handler.fetch(
            self.new_stock_url,
            headers=self.headers,
            cookies=self.cookies,
            params=params,
            method="GET",
        )
        return response.json() if response else None

    def get_jg_report_list(self, page: int, q_type: str) -> Optional[Dict]:
        params = {
            "cb": "",
            "pageSize": "50",
            "beginTime": self.begin_time,
            "endTime": self.end_time,
            "pageNo": str(page),
            "fields": "",
            "qType": q_type,
            "orgCode": "",
            "author": "",
            "p": str(page),
            "pageNum": str(page),
            "pageNumber": str(page),
            "_": str(int(datetime.datetime.now().timestamp() * 1000)),
        }
        response = self.single_handler.fetch(
            self.jg_url,
            headers=self.headers,
            cookies=self.cookies,
            params=params,
            method="GET",
        )
        return response.json() if response else None

    def run_category(
        self,
        category_name: str,
        fetch_fn: Callable[[int], Optional[Dict]],
        channel_name: str,
        start_page: int,
    ) -> None:
        total_page = start_page + 100
        page = start_page
        while page <= total_page:
            res_json = fetch_fn(page)
            if res_json:
                total_page = res_json.get("TotalPage", total_page)
                report_list = self.extract_report_list(res_json, channel_name)
                if report_list:
                    self.save_result(insert_list=report_list)
                    self.log_print.print(f"{category_name}  {page}/{total_page}  saved {len(report_list)} reports")
                else:
                    page_info = {"category": category_name, "page": page}
                    self.error_page_set.add_to_set(self._encode_cache(page_info))
                self.log_page.record_int(page)
                page += 1
            else:
                page_info = {"category": category_name, "page": page}
                self.error_page_set.add_to_set(self._encode_cache(page_info))
                page += 1

    def handle_error_page(self):
        error_page_set = list(self.error_page_set.get_set_members())
        if not error_page_set:
            return True
        for error_key in error_page_set:
            page_info = self._decode_cache(error_key)
            category = page_info.get("category")
            page = page_info.get("page")
            runner = self.get_category_runner(category)
            if not runner:
                self.error_page_set.remove_from_set(error_key)
                continue
            self.log_print.warning(f"retry {category} page {page}")
            runner(page)
            self.error_page_set.remove_from_set(error_key)
        return len(self.error_page_set.get_set_members()) == 0

    def get_category_runner(self, category_name: str) -> Optional[Callable[[int], None]]:
        if category_name == "个股研报":
            return lambda page: self.run_category(
                category_name, self.get_stock_report_list, "个股研报", page
            )
        if category_name == "行业研报":
            return lambda page: self.run_category(
                category_name, self.get_industry_report_list, "行业研报", page
            )
        if category_name == "新股研报":
            return lambda page: self.run_category(
                category_name, self.get_new_stock_report_list, "新股研报", page
            )
        if category_name == "宏观研报":
            return lambda page: self.run_category(
                category_name, lambda p: self.get_jg_report_list(p, "3"), "宏观研报", page
            )
        if category_name == "策略报告":
            return lambda page: self.run_category(
                category_name, lambda p: self.get_jg_report_list(p, "2"), "策略报告", page
            )
        if category_name == "券商晨报":
            return lambda page: self.run_category(
                category_name, lambda p: self.get_jg_report_list(p, "4"), "券商晨报", page
            )
        return None

    def run_all(self):
        start_page = self.log_page.get_int(default=1)
        working_cat = self.log_category.get_string()
        categories_finished = self.log_category_finished.get_list()
        report_categories: List[Tuple[str, Callable[[int], Optional[Dict]], str]] = [
            ("个股研报", self.get_stock_report_list, "个股研报"),
            ("行业研报", self.get_industry_report_list, "行业研报"),
            ("新股研报", self.get_new_stock_report_list, "新股研报"),
            ("宏观研报", lambda p: self.get_jg_report_list(p, "3"), "宏观研报"),
            ("策略报告", lambda p: self.get_jg_report_list(p, "2"), "策略报告"),
            ("券商晨报", lambda p: self.get_jg_report_list(p, "4"), "券商晨报"),
        ]
        for category_name, fetch_fn, channel_name in report_categories:
            if category_name in categories_finished and category_name != "券商晨报":
                self.log_print.print(f"{category_name}已完成，跳过...")
                continue
            self.log_print.print(f"开始抓取{category_name}...")
            self.log_category.record_string(category_name)
            if category_name == working_cat:
                self.run_category(category_name, fetch_fn, channel_name, start_page)
                self.log_page.clear_value()
            else:
                self.run_category(category_name, fetch_fn, channel_name, 1)
                self.log_page.clear_value()
            self.log_category_finished.append_to_list(category_name)

        while True:
            finished_page = self.handle_error_page()
            if finished_page:
                break

    def test_first_pages(self) -> Dict[str, Dict[str, int]]:
        results = {}
        test_targets = [
            ("个股研报", self.get_stock_report_list, "个股研报"),
            ("行业研报", self.get_industry_report_list, "行业研报"),
            ("新股研报", self.get_new_stock_report_list, "新股研报"),
            ("宏观研报", lambda p: self.get_jg_report_list(p, "3"), "宏观研报"),
            ("策略报告", lambda p: self.get_jg_report_list(p, "2"), "策略报告"),
            ("券商晨报", lambda p: self.get_jg_report_list(p, "4"), "券商晨报"),
        ]
        for category_name, fetch_fn, channel_name in test_targets:
            res_json = fetch_fn(1)
            if not res_json:
                self.log_print.warning(f"test_first_pages failed: {category_name}")
                results[category_name] = {"total": 0, "count": 0}
                continue
            total = res_json.get("Total", 0) or 0
            report_list = self.extract_report_list(res_json, channel_name)
            results[category_name] = {"total": int(total), "count": len(report_list)}
            self.log_print.print(
                f"test_first_pages {category_name}: total={results[category_name]['total']} count={results[category_name]['count']}"
            )
        return results


if "__main__" == __name__:
    test_url = "https://data.eastmoney.com/report/"
    spider = Spider(pro_path=Path(__file__).parent, test_url=test_url)
    # spider.test_first_pages()
    spider.run_all()

"""
naver_research/spider.py
────────────────────────
示例：韩国 Naver Finance 研究报告 (https://finance.naver.com/research/) 列表抓取 ——
国际站点采集 + 自定义解析模块。

演示内容：
  1. 国际站点采集：6 类研究报告（行情信息 / 投资信息 / 个股分析 / 行业分析 /
     经济分析 / 债券分析），每类一个 GET 分页 HTML 列表页（type_url + page 参数）
  2. 自定义解析模块：HTML 解析逻辑独立成 parser.py —— 按 type_url 路由到 6 个
     解析函数（_PARSER_MAP），统一处理韩文表头、分隔行、附件列、日期格式
  3. 分类维度断点续爬：
     - log_category        记录当前正在抓取的分类
     - log_page            记录当前分类下的页码
     - log_category_finished 记录已完成分类（下次直接跳过）
     - error_page_set      收集失败页 (type_url, page)，主流程后统一重试
  4. _id 由详情 URL 生成（generate_string_id），按 URL 去重；
     数据带韩文分类标签（type_kr）与中文分类名（type_cn）

注意：
  - 本站无需登录，self.cookies 留空即可
  - parser.py 的 __main__ 自测依赖本地保存的 HTML 快照（html_*.html），
    本示例未附带这些快照文件，仅供解析逻辑开发期参考
"""

import json
import datetime
from pathlib import Path
from typing import Dict, List, Optional

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id

from parser import get_total_pages, parse_page, TYPE_LIST


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Chromium\";v=\"147\", \"Not/A)Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\"",
        }
        # 站点无需登录会话，留空即可
        self.cookies = {}
        self.log_category = Cache(f"{self.table_name}_log_category")
        self.log_page = Cache(f"{self.table_name}_log_page")
        self.log_category_finished = Cache(f"{self.table_name}_log_category_finished")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    def get_list_page(self, type_url: str, page: int) -> Optional[str]:
        params = {"page": str(page)}
        response = self.single_fetcher.fetch(
            type_url,
            headers=self.headers,
            cookies=self.cookies,
            params=params,
            method="GET",
        )
        if response and response.status_code == 200:
            return response.text
        return None

    def decorate_list(self, data_list: List[Dict]) -> List[Dict]:
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for item in data_list:
            detail_url = item.get("detail_url", "")
            item["_id"] = item.get("_id") or generate_string_id(detail_url) if detail_url else generate_string_id(str(item))
            item["create_time"] = now_ts
        return data_list

    def handle_error_page(self) -> bool:
        error_page_set = list(self.error_page_set.get_set_members())
        if not error_page_set:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True
        for error_key in error_page_set:
            page_info = self._decode_cache(error_key)
            type_url = page_info.get("type_url")
            page = page_info.get("page")

            html = self.get_list_page(type_url, page)
            if html:
                data_list = parse_page(html, type_url)
                if data_list:
                    data_list = self.decorate_list(data_list)
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(
                    f"handle_error_page type_url:{type_url} page:{page} 采集失败"
                )
                return False
        return len(self.error_page_set.get_set_members()) == 0

    def run_type(self, type_info: Dict, start_page: int) -> None:
        type_kr = type_info.get("type_kr")
        type_url = type_info.get("type_url")
        self.log_print.print(f"开始抓取 {type_kr}...")
        initial_html = self.get_list_page(type_url, 1)
        if not initial_html:
            self.log_print.error(f"{type_kr} 首页获取失败")
            return
        total_pages = get_total_pages(initial_html)
        self.log_print.print(f"{type_kr} 共有 {total_pages} 页")
        start_page = self.log_page.get_int(default=start_page)
        for page in range(start_page, total_pages + 1):
            html = self.get_list_page(type_url, page) if page > 1 else initial_html
            if html:
                data_list = parse_page(html, type_url)
                if data_list:
                    data_list = self.decorate_list(data_list)
                    self.save_result(insert_list=data_list)
                    self.log_print.print(f"  [{type_kr}] p:{page}/{total_pages} 采集成功 {len(data_list)} 条")
                else:
                    self.log_print.warning(f"  [{type_kr}] p:{page}/{total_pages} 解析无数据")
            else:
                page_info = {"type_url": type_url, "page": page}
                self.log_print.print(f"  [{type_kr}] p:{page}/{total_pages} 列表采集失败")
                self.error_page_set.add_to_set(self._encode_cache(page_info))
            self.log_page.record_int(page)

        self.log_print.print(f"{type_kr} 列表页面抓取完成")

    def run_all(self):
        start_page = self.log_page.get_int(default=1)
        working_cat = self.log_category.get_string()
        categories_finished = self.log_category_finished.get_list()
        for type_info in TYPE_LIST:
            type_kr = type_info.get("type_kr")
            if type_kr in categories_finished:
                self.log_print.print(f"{type_kr}已完成，跳过...")
                continue

            self.log_category.record_string(type_kr)
            if type_kr == working_cat:
                self.run_type(type_info, start_page)
                self.log_page.clear_value()
            else:
                self.run_type(type_info, 1)
                self.log_page.clear_value()

            self.log_category_finished.append_to_list(type_kr)

        while True:
            self.log_print.warning("主流程采集完成，开始处理错误的 page")
            finished_page = self.handle_error_page()
            if finished_page:
                break


if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_all()

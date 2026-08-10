"""
medlive_guide/spider.py
────────────────────────
示例：医脉通指南 (https://guide.medlive.cn) 指南列表抓取 —— 单分类全量模式。

演示内容：
  1. SingleRequestHandler 发起 POST 表单请求（more_filter 接口），
     解析返回 JSON 中内嵌的 HTML 片段
  2. BeautifulSoup 解析列表结构（guideItem / guideTitle / guideLine2 / guideBtmInfo）
  3. Redis 断点续爬：log_page 记录已抓页码，error_page_set 收集失败页并自动重试
  4. 使用 generate_string_id(url) 生成 MongoDB _id，按 URL 去重

与本目录 spider_type.py（按科室分类遍历）对比，展示同一站点的两种列表抓取策略。

注意：
  - 站点的会话凭证（PHPSESSID / XSRF-TOKEN / laravel_session 与 csrf_token）
    已清空；运行前请从浏览器开发者工具中获取有效会话后填入
    self.cookies 与 self.csrf_token。
"""

import json
import datetime
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.base_url = "https://guide.medlive.cn"
        self.list_api_url = "https://guide.medlive.cn/more_filter"

        self.headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://guide.medlive.cn",
            "Pragma": "no-cache",
            "Referer": "https://guide.medlive.cn/guide/filter?sub_type=0&category=0&category_sec=0&year=0&cn_flg=0",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\""
        }
        # 会话凭证（PHPSESSID / XSRF-TOKEN / laravel_session）已移除，
        # 运行前请填入自己浏览器中的有效值
        self.cookies = {
            "PHPSESSID": "",
            "XSRF-TOKEN": "",
            "laravel_session": "",
            "Hm_lvt_62d92d99f7c1e7a31a11759de376479f": "1777285949",
            "Hm_lpvt_62d92d99f7c1e7a31a11759de376479f": "1777286686",
            "_pk_id.3.a971": "a4afa81e24359b29.1777286686.1.1777286686.1777286686.",
            "_pk_ses.3.a971": "*"
        }
        self.csrf_token = ""
        self.log_page = Cache(f"{self.table_name}_log_page")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    def get_list_page(self, page: int) -> Optional[Dict]:
        data = {
            "sub_type": "0",
            "category": "0",
            "category_sec": "0",
            "year": "0",
            "cn_flg": "0",
            "page": str(page),
            "page_size": "10",
            "_token": self.csrf_token
        }

        response = self.single_fetcher.fetch(
            self.list_api_url,
            headers=self.headers,
            cookies=self.cookies,
            data=data,
            method="POST",
        )
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                self.log_print.error(f"JSON decode error: {e}")
                return None
        return None

    def parse_html_data(self, html_content: str) -> List[Dict]:
        soup = BeautifulSoup(html_content, "html.parser")
        guidelines = []
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            url = href if href.startswith("http") else self.base_url + href

            guide_item = a_tag.find("div", class_="guideItem")
            if not guide_item:
                continue

            title_div = guide_item.find("div", class_="guideTitle")
            title = title_div.get_text(strip=True) if title_div else ""

            line2_div = guide_item.find("div", class_="guideLine2")
            organization = line2_div.get_text(strip=True) if line2_div else ""

            publish_time = ""
            views = ""
            btm_info = guide_item.find("div", class_="guideBtmInfo")
            if btm_info:
                time_span = btm_info.find("span", class_="guideBtmTime")
                publish_time = time_span.get_text(strip=True) if time_span else ""
                if "发布" in publish_time:
                    publish_time = publish_time.split("发布")[0].strip()

                num_span = btm_info.find("span", class_="guideBtmNum")
                views = num_span.get_text(strip=True) if num_span else ""
                if "人" in views:
                    views = views.split("人")[0].strip()

            guidelines.append({
                "_id": generate_string_id(url),
                "标题": title,
                "url": url,
                "发表机构": organization,
                "发布时间": publish_time,
                "浏览量": views,
                "create_time": now_ts
            })

        return guidelines

    def handle_error_page(self) -> bool:
        error_page_set = list(self.error_page_set.get_set_members())
        if not error_page_set:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True

        for error_key in error_page_set:
            page_info = self._decode_cache(error_key)
            page = page_info.get("page")

            res_json = self.get_list_page(page)
            if res_json and str(res_json.get("code")) == "200":
                html_content = res_json.get("data", "")
                data_list = self.parse_html_data(html_content)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(f"handle_error_page page:{page} 采集失败")
                return False

        return len(self.error_page_set.get_set_members()) == 0

    def run_all(self):
        start_page = self.log_page.get_int(default=1)
        page = start_page
        has_more = "Y"

        self.log_print.print(f"开始从第 {page} 页抓取医脉通指南列表...")

        while has_more and has_more == "Y":
            res_json = self.get_list_page(page)
            if res_json and str(res_json.get("code")) == "200":
                html_content = res_json.get("data", "")
                data_list = self.parse_html_data(html_content)
                has_more = res_json.get("has_more", False)
                if data_list:
                    self.save_result(insert_list=data_list)
                    self.log_print.print(f"  page:{page} 采集成功 {len(data_list)} 条, has_more={has_more}")
                else:
                    self.log_print.warning(f"  page:{page} 解析无数据, has_more={has_more}")
            else:
                page_info = {"page": page}
                self.log_print.print(f"  page:{page} 列表请求失败")
                self.error_page_set.add_to_set(self._encode_cache(page_info))

            self.log_page.record_int(page)
            page += 1

        self.log_print.print("主流程采集完成")
        self.log_page.clear_value()

        for retry in range(3):
            self.log_print.warning("开始处理错误的 page")
            finished_page = self.handle_error_page()
            if finished_page:
                break


if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_all()

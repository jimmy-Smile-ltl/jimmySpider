"""
medlive_guide/spider_type.py
─────────────────────────────
示例：医脉通指南 (https://guide.medlive.cn) 指南列表抓取 —— 多分类遍历模式。

演示内容：
  1. 从 category_list.json 读取「一级科室 → 二级科室」分类树
  2. 双重循环遍历每个 (category, category_sec) 组合，逐页抓取该分类下的指南
  3. 断点续爬：将 (cat_idx, sec_idx, page) 进度序列化写入 Redis，
     中断后可从上次位置恢复
  4. 错误页重试：失败时记录分类与页码上下文，统一重试

与 spider.py（单分类全量模式）对比，展示同一站点的两种列表抓取策略。

注意：
  - 站点的会话凭证（PHPSESSID / XSRF-TOKEN / laravel_session 与 csrf_token）
    已清空，运行前请填入自己浏览器中的有效值。
  - category_list.json 为运行时依赖的分类数据文件，需与本文件同目录。
"""

import os
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
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = Path(__file__).parent
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.base_url = "https://guide.medlive.cn"
        self.list_api_url = "https://guide.medlive.cn/more_filter"
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": "https://guide.medlive.cn/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\""
        }
        # 会话凭证（PHPSESSID / XSRF-TOKEN / laravel_session）已移除，
        # 运行前请填入自己浏览器中的有效值
        self.cookies = {
            "PHPSESSID": "",
            "Hm_lvt_62d92d99f7c1e7a31a11759de376479f": "1777285949",
            "ymt_pk_id": "a4afa81e24359b29",
            "HMACCOUNT": "9C17577EF520A8CE",
            "_pk_ses.3.a971": "*",
            "Hm_lpvt_62d92d99f7c1e7a31a11759de376479f": "1777342197",
            "_pk_id.3.a971": "a4afa81e24359b29.1777286686.2.1777342197.1777287112.",
            "XSRF-TOKEN": "",
            "laravel_session": ""
        }
        self.csrf_token = ""
        self.log_page = Cache(f"{self.table_name}_log_page_type")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set_type")
        # Load category list
        category_file = os.path.join(Path(__file__).parent, "category_list.json")
        with open(category_file, "r", encoding="utf-8") as f:
            self.category_list = json.load(f)

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    def get_list_page(self, page: int, category_value: str, sec_value: str) -> Optional[Dict]:
        data = {
            "sub_type": "0",
            "category": category_value,
            "category_sec": sec_value,
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
            check_size=False
        )
        if response and response.status_code == 200:
            try:
                return response.json()
            except Exception as e:
                self.log_print.error(f"JSON decode error: {e}")
                return None
        return None

    def parse_html_data(self, html_content: str, cat_name: str, sec_name: str) -> List[Dict]:
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
                "科室": cat_name,
                "二级科室": sec_name,
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
            cat_val = page_info.get("category_value")
            sec_val = page_info.get("sec_value")
            cat_name = page_info.get("cat_name", "")
            sec_name = page_info.get("sec_name", "")
            res_json = self.get_list_page(page, cat_val, sec_val)
            if res_json and str(res_json.get("code")) == "200":
                html_content = res_json.get("data", "")
                data_list = self.parse_html_data(html_content, cat_name, sec_name)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            elif res_json and str(res_json.get("code")) == "500" and "没有更多数据" in res_json.get("msg", ""):
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(f"handle_error_page page:{page} cat:{cat_val} sec:{sec_val} 采集失败")
                return False
        return len(self.error_page_set.get_set_members()) == 0

    def run_all(self):
        progress_str = self.log_page.get_string(default="")
        if progress_str:
            progress = json.loads(progress_str)
            start_cat_idx = progress.get("cat_idx", 0)
            start_sec_idx = progress.get("sec_idx", 0)
            start_page = progress.get("page", 1)
        else:
            start_cat_idx = 0
            start_sec_idx = 0
            start_page = 1
        self.log_print.print(f"开始抓取医脉通指南分类列表, 恢复自 cat_idx:{start_cat_idx}, sec_idx:{start_sec_idx}, page:{start_page}...")
        for cat_idx in range(start_cat_idx, len(self.category_list)):
            category = self.category_list[cat_idx]
            cat_name = category.get("type_name")
            cat_val = category.get("type_value")
            sub_list = category.get("sub_category_list", [])
            # Sub-category loop
            sec_start = start_sec_idx if cat_idx == start_cat_idx else 0
            for sec_idx in range(sec_start, len(sub_list)):
                sub_category = sub_list[sec_idx]
                sec_name = sub_category.get("sec_name")
                sec_val = sub_category.get("sec_value")
                page = start_page if (cat_idx == start_cat_idx and sec_idx == start_sec_idx) else 1
                has_more = "Y"
                self.log_print.print(f"开始抓取分类: {cat_name} -> {sec_name}")
                while has_more and has_more == "Y":
                    res_json = self.get_list_page(page, cat_val, sec_val)
                    if res_json and str(res_json.get("code")) == "200":
                        html_content = res_json.get("data", "")
                        data_list = self.parse_html_data(html_content, cat_name, sec_name)
                        has_more = res_json.get("has_more", False)
                        if data_list:
                            self.save_result(insert_list=data_list)
                            self.log_print.print(f"  [{cat_name}-{sec_name}] page:{page} 采集成功 {len(data_list)} 条, has_more={has_more}")
                        else:
                            self.log_print.warning(f"  [{cat_name}-{sec_name}] page:{page} 解析无数据, has_more={has_more}")
                    elif res_json and str(res_json.get("code")) == "500" and "没有更多数据" in res_json.get("msg", ""):
                        self.log_print.print(f"  [{cat_name}-{sec_name}] page:{page} 没有更多数据了")
                        has_more = "N"
                    else:
                        page_info = {
                            "page": page,
                            "category_value": cat_val,
                            "sec_value": sec_val,
                            "cat_name": cat_name,
                            "sec_name": sec_name
                        }
                        self.log_print.print(f"  [{cat_name}-{sec_name}] page:{page} 列表请求失败")
                        self.error_page_set.add_to_set(self._encode_cache(page_info))
                    self.log_page.record_string(json.dumps({
                        "cat_idx": cat_idx,
                        "sec_idx": sec_idx,
                        "page": page + 1 if has_more == "Y" else 1
                    }))
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

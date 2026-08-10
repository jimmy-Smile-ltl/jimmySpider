"""
medsci/spider.py
────────────────
示例：梅斯医学指南库 (https://www.medsci.cn/guideline) 抓取 ——
医学期刊/指南网站的分类遍历采集。

演示内容：
  1. 两级数据流：先 GET columnList 接口拿全部分类（categoryId/categoryName/tenant），
     再按分类 GET /guideline/search?page=N&s_id=X&tenant=Y 分页抓 HTML 列表
  2. 分类维度断点续爬：{cat_idx, page} JSON 序列化写入 Redis，
     中断后从「分类索引 + 页码」精确恢复
  3. 单页多项解析：journal-item 卡片提取标题/URL/一级与二级类型/发布时间/发表机构/概述，
     每类数据自动带「科室」与「科室id」维度
  4. 反爬提示：该站连续约 25 次请求后会触发腾讯滑块验证码 —— 响应仍为 200
     但解析无数据；页间 time.sleep(5) 限速可降低触发概率，
     触发后需在浏览器中完成验证并更新 Cookie（见 self.cookies 注释）

数据字段：标题、科室、科室id、指南类型、二级类型、发表机构、发布时间、概述、url。
"""

import json
import time
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
        pro_path = str(Path(__file__).parent)
        self.pro_path = pro_path
        if not kwargs.get("pro_path"):
            kwargs["pro_path"] = self.pro_path
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://www.medsci.cn"
        self.column_api_url = "https://www.medsci.cn/medsciCommon/index/columnList"
        self.list_page_url = "https://www.medsci.cn/guideline/search"

        self.headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "pragma": "no-cache",
            "priority": "u=1, i",
            "referer": "https://www.medsci.cn/guideline/index.do",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        }
        # 会话 Cookie 已清空（原实现包含滑块验证码通过后的 Cookie，如 tfstk / JSESSIONID 等）。
        # 若触发腾讯滑块验证码（响应 200 但解析无数据），请在浏览器中完成验证后，
        # 把新的 Cookie 填入 self.cookies。
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
    #  Step 1: Fetch category list                                         #
    # ------------------------------------------------------------------ #

    def get_category_list(self) -> List[Dict]:
        """
        GET /medsciCommon/index/columnList
        Returns categoryDtos list, each: {categoryId, categoryName, tenant}
        """
        response = self.single_fetcher.fetch(
            self.column_api_url,
            headers=self.headers,
            cookies={},
            method="GET",
            check_size=False,
        )
        if response and response.status_code == 200:
            try:
                res_json = response.json()
                category_list = res_json.get("data", {}).get("categoryDtos", [])
                self.log_print.print(f"get_category_list: 共 {len(category_list)} 个分类")
                return category_list
            except Exception as e:
                self.log_print.error(f"Category JSON decode error: {e}")
        return []

    # ------------------------------------------------------------------ #
    #  Step 2: Fetch list page (HTML)                                      #
    # ------------------------------------------------------------------ #

    def get_list_page(self, page: int, category_id: int, tenant: int) -> Optional[object]:
        """
        GET /guideline/search?page=N&s_id=X&tenant=Y
        Returns raw response object (HTML).
        """
        params = {
            "page": str(page),
            "s_id": str(category_id),
            "tenant": str(tenant),
        }
        response = self.single_fetcher.fetch(
            self.list_page_url,
            headers=self.headers,
            cookies=self.cookies,
            params=params,
            method="GET",
            check_size=False,
        )
        if response and response.status_code == 200:
            return response
        return None

    # ------------------------------------------------------------------ #
    #  Step 3: Parse HTML                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_total_page(soup: BeautifulSoup) -> int:
        """
        Extract total page count from:
        <span class="page-info-right">页码: <span class="page-now">2</span>/34页</span>
        """
        try:
            page_info = soup.select_one("span.page-info-right")
            if page_info:
                text = page_info.get_text(strip=True)               # "页码:2/34页"
                text = text.replace("页码:", "").replace("页", "").strip()  # "2/34"
                return int(text.split("/")[1])
        except Exception:
            pass
        return 1

    @staticmethod
    def _parse_item(item) -> Optional[Dict]:
        """Parse a single <div class="journal-item"> into a flat dict."""
        try:
            title_tag = item.find("a", class_="ms-link")
            if not title_tag:
                return None
            span = title_tag.find("span")
            title = span.get_text(strip=True) if span else title_tag.get_text(strip=True)
            url = title_tag.get("href", "")

            # 指南类型 / 二级类型
            type_spans = item.find_all("span", class_="item-label")
            primary_type = type_spans[0].get_text(strip=True) if len(type_spans) > 0 else ""
            secondary_type = type_spans[1].get_text(strip=True) if len(type_spans) > 1 else ""

            # 发布时间
            more_detail = item.find("div", class_="more-detail")
            date_span = more_detail.find("p").find("span") if more_detail else None
            publish_time = date_span.get_text(strip=True) if date_span else ""

            # 发表机构
            org_a = None
            if more_detail:
                ps = more_detail.find_all("p")
                if len(ps) > 1:
                    org_a = ps[1].find("a")
            organization = org_a.get_text(strip=True) if org_a else ""

            # 概述（最后一个 <p>）
            all_p = item.find_all("p")
            abstract = all_p[-1].get_text(strip=True) if all_p else ""

            return {
                "url": url,
                "标题": title,
                "指南类型": primary_type,
                "二级类型": secondary_type,
                "发布时间": publish_time,
                "发表机构": organization,
                "概述": abstract,
            }
        except Exception:
            return None

    def parse_html_data(self, html_text: str, category: Dict) -> tuple:
        """
        Parse full HTML response.
        Returns (data_list, total_page).

        字段对齐（与同项目其他 spider 保持一致）：
            _id        — URL 生成的唯一 ID
            标题        — 指南名称
            科室        — 一级分类名
            科室id      — 分类 ID
            指南类型    — 指南 / 共识 / 其它 …
            二级类型    — 子标签
            发表机构    — 发布机构名称
            发布时间    — 字符串日期
            概述        — 摘要文本
            url        — 详情页链接
            create_time — 抓取时间
        """
        soup = BeautifulSoup(html_text, "html.parser")
        total_page = self._parse_total_page(soup)

        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cat_name = category.get("categoryName", "")
        cat_id = category.get("categoryId", "")

        results = []
        for journal in soup.select("div.journal-list div.journal-item"):
            item = self._parse_item(journal)
            if not item:
                continue
            item["_id"] = generate_string_id(item["url"])
            item["科室"] = cat_name
            item["科室id"] = cat_id
            item["create_time"] = now_ts
            results.append(item)

        return results, total_page

    # ------------------------------------------------------------------ #
    #  Step 4: Retry error pages                                           #
    # ------------------------------------------------------------------ #

    def handle_error_page(self, category_map: Dict) -> bool:
        """Re-fetch any pages that previously failed."""
        error_keys = list(self.error_page_set.get_set_members())
        if not error_keys:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True

        for error_key in error_keys:
            page_info = self._decode_cache(error_key)
            page = page_info.get("page")
            cat_id = page_info.get("cat_id")
            tenant = page_info.get("tenant", 100)
            category = category_map.get(cat_id, {"categoryId": cat_id, "categoryName": ""})

            response = self.get_list_page(page, cat_id, tenant)
            if response:
                data_list, _ = self.parse_html_data(response.text, category)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_page_set.remove_from_set(error_key)
            else:
                self.log_print.print(
                    f"handle_error_page page:{page} cat_id:{cat_id} 采集失败"
                )
                return False

        return len(self.error_page_set.get_set_members()) == 0

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        # ---------- load / resume progress ----------
        progress_str = self.log_page.get_string(default="")
        if progress_str:
            progress = json.loads(progress_str)
            start_cat_idx = progress.get("cat_idx", 0)
            start_page = progress.get("page", 1)
        else:
            start_cat_idx = 0
            start_page = 1

        # ---------- fetch category list ----------
        category_list = self.get_category_list()
        if not category_list:
            self.log_print.error("run_all: 分类列表为空，终止")
            return

        category_map = {c["categoryId"]: c for c in category_list}

        self.log_print.print(
            f"开始抓取 medsci 指南列表, "
            f"恢复自 cat_idx:{start_cat_idx}, page:{start_page}..."
        )

        # ---------- iterate categories ----------
        for cat_idx in range(start_cat_idx, len(category_list)):
            category = category_list[cat_idx]
            cat_id = category.get("categoryId")
            cat_name = category.get("categoryName", "")
            tenant = category.get("tenant", 100)

            page = start_page if cat_idx == start_cat_idx else 1
            total_page = None

            self.log_print.print(f"开始抓取分类: {cat_name} (id={cat_id})")

            while True:
                response = self.get_list_page(page, cat_id, tenant)
                if response:
                    data_list, total_page = self.parse_html_data(response.text, category)
                    if data_list:
                        self.save_result(insert_list=data_list)
                        self.log_print.print(
                            f"  [{cat_name}] page:{page}/{total_page} "
                            f"采集成功 {len(data_list)} 条"
                        )
                    else:
                        self.log_print.warning(
                            f"  [{cat_name}] page:{page}/{total_page} 解析无数据"
                        )
                        page_info = {"page": page, "cat_id": cat_id, "tenant": tenant}
                        self.log_print.print(
                            f"  [{cat_name}] page:{page} 列表请求失败，记录错误页"
                        )
                        self.error_page_set.add_to_set(self._encode_cache(page_info))

                    # save progress after every page
                    self.log_page.record_string(
                        json.dumps({"cat_idx": cat_idx, "page": page + 1})
                    )

                    if total_page is not None and page >= total_page:
                        self.log_print.print(f"  [{cat_name}] 已采集完毕")
                        break
                    page += 1
                    time.sleep(5)

                else:
                    page_info = {"page": page, "cat_id": cat_id, "tenant": tenant}
                    self.log_print.print(
                        f"  [{cat_name}] page:{page} 列表请求失败，记录错误页"
                    )
                    self.error_page_set.add_to_set(self._encode_cache(page_info))
                    page += 1
                    time.sleep(5)
                    break   # skip to next category, avoid infinite loop

        self.log_print.print("主流程采集完成")
        self.log_page.clear_value()

        # ---------- retry error pages ----------
        for retry in range(3):
            self.log_print.warning(f"开始处理错误 page (第 {retry + 1} 次)")
            if self.handle_error_page(category_map):
                break


if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_all()

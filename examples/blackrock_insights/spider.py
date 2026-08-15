"""
blackrock_insights/spider.py
────────────────────────────
示例：BlackRock 投资研究院全球洞察归档页抓取
(https://www.blackrock.com/corporate/insights/blackrock-investment-institute/archives)
—— 国际金融网站静态 HTML 列表页解析，无需登录与接口。

演示内容：
  1. GET 静态归档页抓取：archives 归档页一次返回全部洞察条目（无分页）
  2. BeautifulSoup 卡片解析：div.gls-related-literature div.item 中提取
     标题（h2）/ 日期（div.attribution）/ 链接（a）/ 摘要（div.description）
  3. 英文日期标准化：convert_date_robust 将任意格式日期统一转为 YYYY-MM-DD
  4. 相对链接补全：urljoin 拼接详情页完整 URL
  5. _id 降级：URL 缺失时回退为标题 MD5
  6. Redis 完成标记：log_page 记录 done 标记，避免重复采集

数据字段：标题、发布时间、file_url（详情链接）、概述。
"""

import datetime
import json
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.datetime_utils import convert_date_robust
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        # 自动定位项目目录（等价于入口处传 pro_path=Path(__file__).parent）
        kwargs.setdefault("pro_path", Path(__file__).parent)
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://www.blackrock.com"
        self.archives_url = "https://www.blackrock.com/corporate/insights/blackrock-investment-institute/archives"

        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "referer": "https://www.blackrock.com/corporate/insights/blackrock-investment-institute/publications/weekly-commentary",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        }

        self.log_page = Cache(f"{self.table_name}_log_page")

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    # ------------------------------------------------------------------ #
    #  Fetch & parse                                                       #
    # ------------------------------------------------------------------ #

    def get_archives_page(self) -> Optional[str]:
        response = self.single_fetcher.fetch(
            self.archives_url,
            headers=self.headers,
            method="GET",
            check_size=False,
        )
        if response and response.status_code == 200:
            response.encoding = response.apparent_encoding
            return response.text
        return None

    def parse_archives(self, html_text: str) -> List[Dict]:
        soup = BeautifulSoup(html_text, "html.parser")
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        results = []
        for item in soup.select("div.gls-related-literature div.item"):
            title_tag = item.select_one("h2")
            title = title_tag.get_text(strip=True) if title_tag else ""

            date_tag = item.select_one("div.attribution")
            date_str = date_tag.get_text(strip=True) if date_tag else ""
            publish_time = convert_date_robust(date_str) if date_str else ""

            link_tag = item.select_one("a")
            href = link_tag.attrs.get("href") if link_tag else ""
            url = urljoin(self.archives_url, href) if href else ""

            desc_tag = item.select_one("div.description")
            description = desc_tag.get_text(strip=True) if desc_tag else ""

            results.append({
                "_id": generate_string_id(url or title),
                "标题": title,
                "发布时间": publish_time,
                "file_url": url,
                "概述": description,
                "create_time": now_ts,
            })

        return results

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        self.log_print.print("开始抓取 BlackRock 全球洞察 (archives) 列表...")
        html_text = self.get_archives_page()
        if not html_text:
            self.log_print.error("列表请求失败")
            return

        data_list = self.parse_archives(html_text)
        if data_list:
            self.save_result(insert_list=data_list)
            self.log_print.print(f"采集成功 {len(data_list)} 条")
        else:
            self.log_print.warning("解析无数据")

        self.log_page.record_string(self._encode_cache({"done": True}))


if "__main__" == __name__:
    spider = Spider()
    spider.run_all()

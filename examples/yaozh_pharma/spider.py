"""
yaozh_pharma/spider.py
──────────────────────
示例：药智网临床指南数据库 (https://db.yaozh.com/cpg) 列表抓取 ——
中文医药数据库 + 需登录会话的站点。

演示内容：
  1. 登录会话依赖：站点要求已登录的会话 Cookie（PHPSESSID / yaozh_user 等）。
     代码中已清空，运行前请从浏览器登录 db.yaozh.com 后把 Cookie 填入 self.cookies
  2. GET 分页请求：p 参数 + source=sitemap 来源标记，响应为 HTML
  3. 分页信息从 HTML 数据属性读取：div[data-widget=dbPagination] 的
     data-total / data-size 计算总页数（math.ceil(total / size)），
     动态更新 max_page 驱动翻页终止条件
  4. 表格行解析：tbody 中每行 <th> 为年份、4 个 <td> 为
     题目(含链接) / 来源 / 指南制定机构 / 求助全文链接
  5. 页间限速 time.sleep(5)，Redis 断点续爬（log_page）+ 错误页重试

数据字段：发布时间（年份）、题目、题目链接、来源、指南制定机构、求助全文链接。
"""

import json
import time
import math
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
        self.base_url = "https://db.yaozh.com"
        self.list_api_url = "https://db.yaozh.com/cpg"

        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "referer": "https://db.yaozh.com/cpg?source=sitemap",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Linux\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        }
        # 登录会话已清空：本站数据接口需要已登录的会话 Cookie
        # （PHPSESSID / yaozh_user / yaozh_userId / kztoken 等）。
        # 运行前请用浏览器登录 https://db.yaozh.com，在 DevTools 中复制
        # Cookie 填入 self.cookies（支持整体替换为 Cookie 字符串 dict）。
        self.cookies = {}

        self.log_page = Cache(f"{self.table_name}_log_page")
        self.error_page_set = Cache(f"{self.table_name}_error_page_set")

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    def get_list_page(self, page: int) -> Optional[str]:
        params = {
            "p": str(page),
            "source": "sitemap"
        }
        response = self.single_fetcher.fetch(
            self.list_api_url,
            headers=self.headers,
            cookies=self.cookies,
            params=params,
            method="GET",
        )
        if response and response.status_code == 200:
            return response.text
        return None

    def extract_cpg_data(self, html_content: str) -> Dict:
        soup = BeautifulSoup(html_content, 'html.parser')
        pagination = soup.find('div', {'data-widget': 'dbPagination'})
        if pagination:
            total = int(pagination.get('data-total', 0))
            size = int(pagination.get('data-size', 20))
            max_page = math.ceil(total / size) if size > 0 else 0
        else:
            max_page = 0

        table = soup.select_one('table.table.table-striped.zjlsearFromVal')
        records = []
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if table:
            tbody = table.find('tbody')
            if tbody:
                rows = tbody.find_all('tr')
                for row in rows:
                    th_tag = row.find('th')
                    if not th_tag:
                        continue
                    pub_year = th_tag.get_text(strip=True)
                    cells = row.find_all('td')
                    if len(cells) < 4:
                        continue
                    title_tag = cells[0].find('a')
                    title = title_tag.get_text(strip=True) if title_tag else ''
                    title_link = title_tag.get('href', '') if title_tag else ''
                    if title_link and not title_link.startswith('http'):
                        title_link = self.base_url + title_link
                    source = cells[1].get_text(strip=True)
                    organization = cells[2].get_text(strip=True)
                    help_tag = cells[3].find('a')
                    help_link = help_tag.get('href', '') if help_tag else ''
                    records.append({
                        '_id': generate_string_id(title_link) if title_link else generate_string_id(title),
                        '发布时间': pub_year,
                        '题目': title,
                        '题目链接': title_link,
                        '来源': source,
                        '指南制定机构': organization,
                        '求助全文链接': help_link,
                        'create_time': now_ts
                    })

        return {
            'max_page': max_page,
            'list': records
        }

    def handle_error_page(self) -> bool:
        error_page_set = list(self.error_page_set.get_set_members())
        if not error_page_set:
            self.log_print.print("handle_error_page: 无 page 需要处理")
            return True

        for error_key in error_page_set:
            page_info = self._decode_cache(error_key)
            page = page_info.get("page")
            html_content = self.get_list_page(page)
            if html_content:
                parsed_data = self.extract_cpg_data(html_content)
                data_list = parsed_data.get('list', [])
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
        max_page = None

        self.log_print.print(f"开始从第 {page} 页抓取药智数据临床诊疗指南列表...")

        while True:
            html_content = self.get_list_page(page)
            time.sleep(5)
            if html_content:
                parsed_data = self.extract_cpg_data(html_content)
                data_list = parsed_data.get('list', [])
                if max_page is None:
                    max_page = parsed_data.get('max_page', 0)
                    self.log_print.print(f"解析到共有最大分也: {max_page}")
                if data_list:
                    self.save_result(insert_list=data_list)
                    self.log_print.print(f"  page:{page}/{max_page} 采集成功 {len(data_list)} 条")
                else:
                    self.log_print.warning(f"  page:{page}/{max_page} 解析无数据")
            else:
                page_info = {"page": page}
                self.log_print.print(f"  page:{page}/{max_page} 列表请求失败")
                self.error_page_set.add_to_set(self._encode_cache(page_info))

            self.log_page.record_int(page)
            if max_page is not None and page >= max_page:
                break
            page += 1

        self.log_print.print("主流程采集完成")
        self.log_page.clear_value()

        while True:
            self.log_print.warning("开始处理错误的 page")
            finished_page = self.handle_error_page()
            if finished_page:
                break


if "__main__" == __name__:
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run_all()

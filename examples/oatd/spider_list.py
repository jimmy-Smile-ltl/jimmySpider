"""
Example: oatd — Open Access Theses and Dissertations (oatd.org), list-only spider.

A list-only variant of the oatd example (spider.py fetches detail pages).
Demonstrates:
- Pagination pattern: Solr-style search results are fetched page by page;
  once the first page reveals the total match count, all remaining pages are
  requested concurrently via ThreadPoolExecutor (the async alternative —
  AsyncRequestHandler.fetch_all — is used by spider.py for the same site).
- SingleRequestHandler: all sequential page fetches go through it
  (proxy rotation with test validation, anti-bot detection and retries built in).
- Cookie refresh/renewal: 403 "Just a moment..." Cloudflare challenges trigger
  a headless-Chrome CookieFlush guarded by a threading.Lock so concurrent
  threads never flush twice.
- Redis-backed resume: per-year finished set, current-start cursor, and an
  error-page set for URLs that returned empty/bad responses.

Run:  python examples/oatd/spider_list.py
"""

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from cookie_flush_playwright_cdp import CookieFlush
from jimmyspider.cache import Cache
from jimmyspider.html import handleHTML
from jimmyspider.log_print import LogPrint
from jimmyspider.mongo import HandleMongoDB
from jimmyspider.request import AsyncRequestHandler, SingleRequestHandler
from jimmyspider.tool import generate_doi_id, generate_title_id

# 将脚本所在目录加入模块搜索路径，保证同目录的 cookie_flush_playwright_cdp 可导入
sys.path.insert(0, str(Path(__file__).parent))


class SpiderOATD():
    def __init__(self):
        self.table_name = "oatd"
        self.global_lock = Lock()
        self.log_print = LogPrint(name="SpiderOATD")
        # 日志
        self.db_manager = HandleMongoDB(table_name=self.table_name)
        self.html_saver = handleHTML(pro_name=self.table_name)
        self.port = 9229
        # self.cookie_flusher = CookieFlush(port=self.port, headless=False)
        self.flush_time = 0
        self.start_time = time.time()
        self.insert_num = 0
        self.page_size = 30
        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "max-age=0",
            "priority": "u=0, i",
            "referer": "https://oatd.org/oatd/search",
            "sec-ch-ua": "\"Google Chrome\";v=\"147\", \"Not.A/Brand\";v=\"8\", \"Chromium\";v=\"147\"",
            "sec-ch-ua-arch": "\"x86\"",
            "sec-ch-ua-bitness": "\"64\"",
            "sec-ch-ua-full-version": "\"147.0.7727.55\"",
            "sec-ch-ua-full-version-list": "\"Google Chrome\";v=\"147.0.7727.55\", \"Not.A/Brand\";v=\"8.0.0.0\", \"Chromium\";v=\"147.0.7727.55\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-model": "\"\"",
            "sec-ch-ua-platform": "\"Linux\"",
            "sec-ch-ua-platform-version": "\"\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        }
        # cf_clearance 等关键 cookie 由 CookieFlush 在运行时刷新获取，无需硬编码
        self.cookies = {}
        self.search_url  = "https://oatd.org/oatd/search?q=%2A%3A%2A%20AND%20pub_dt%3A%5B1801-01-01T00%3A00%3A00Z%20TO%201802-01-01T00%3A00%3A00Z%5D"
        # 配置真实 test_url 后会自动启用代理轮换（ProxyUtil 用 test_url 验证代理有效性）
        self.test_url = None
        self.async_handler = AsyncRequestHandler(
            max_workers=10,
            test_url=self.test_url,  # 测试链接，避免请求过多导致IP被封
        )
        self.single_handler = SingleRequestHandler(
            test_url=self.test_url,  # 测试链接，避免请求过多导致IP被封
        )
        self.log_finished_year = Cache(f"{self.table_name}_log_finished_year")
        self.log_current_start = Cache(f"{self.table_name}_log_current_start")
        self.log_error_page_url = Cache(f"{self.table_name}_log_error_page_url")
        self.start_year = 2000
        self.end_year = datetime.now().year
        self.has_flush = True

    def print_efficiency(self, insert_list):
        if type(insert_list) == list:
            self.db_manager.insert_many(insert_list)
            self.insert_num += len(insert_list)
        elif type(insert_list) == dict:
            self.db_manager.insert_one(insert_list)
            self.insert_num +=  1
        cost_time = time.time() - self.start_time
        self.log_print.print(f"插入效率计算 {self.insert_num/int(cost_time):.4f} 行/秒, 总运行时间：{int(cost_time)}  插入总行数 {self.insert_num}")


    def flush_cookies(self, url):
        now_time = time.time()
        # 60秒之内 不再刷新 等会多重新尝试就好了
        if abs(now_time - self.flush_time) < 60:
            while not self.has_flush:
                time.sleep(60)
                print("等待cookie刷新", end="\t")
            if not self.has_flush:
                print("cookie刷新完毕", end="\t")
        else:
            self.has_flush = False
            self.flush_time = time.time()
            self.cookie_flusher = CookieFlush(port=self.port, headless=False)
            cookies = self.cookie_flusher.flush(url)
            print(f"cookie flush : {cookies}" )
            self.cookies.update(cookies)
            self.has_flush = True

    # 翻页
    def search_one_page(self, page_url):
        start_time = time.time()
        retry_count = 20
        retry = 1
        while True:
            retry += 1
            try:
                response = self.single_handler.fetch(page_url, headers=self.headers, cookies=self.cookies)
                if not response:
                    print("error search_one_page 请求失败，返回 None 尝试刷新 cookie")
                    with self.global_lock:
                        self.flush_cookies(self.search_url)
                        time.sleep(2)
                        continue
            except Exception as e:
                time.sleep(5)
                print(f"search_one_page error {e}")
                continue
            if response and response.status_code == 200 and "Search Limiters" in response.text:
                end_time = time.time()
                self.log_print.print(f"search_one_page retry:{retry} ,耗时 {end_time - start_time:.4f}  {page_url}")
                return response
            elif "Server Too Busy - Please slow down" in response.text:
                print(f"需要休息 slow down 默认5s 累积 {retry * 5 }s  {retry }/ {retry_count}")
                time.sleep(5 * retry)
            elif response.status_code == 403 and "Just a moment..." in response.text:
                with self.global_lock:
                    self.flush_cookies(self.search_url)
                    time.sleep(2)
                    continue
            else:  # 待分析
                print("这个一般是 params 问题导致的 异常  bad request 直接放弃 但是记录 ")
                # 比如下面这个 啥都没有 空白 其他页有可以
                # https://oatd.org/oatd/search?q=%2A%3A%2A%20AND%20pub_dt%3A%5B1801-01-01T00%3A00%3A00Z%20TO%201802-01-01T00%3A00%3A00Z%5D&q=%2A%3A%2A+AND+pub_dt%3A%5B2014-01-01T00%3A00%3A00Z+TO+2015-01-01T00%3A00%3A00Z%5D&start=572221
                # https://oatd.org/oatd/search?q=%2A%3A%2A%20AND%20pub_dt%3A%5B1801-01-01T00%3A00%3A00Z%20TO%201802-01-01T00%3A00%3A00Z%5D&q=%2A%3A%2A+AND+pub_dt%3A%5B2014-01-01T00%3A00%3A00Z+TO+2015-01-01T00%3A00%3A00Z%5D&start=572161
                print(page_url)
                self.log_error_page_url.add_to_set(page_url)
                return None
            if retry > retry_count:
                self.log_print.error(f"search_oatd 多次尝试  {retry}  后仍无法成功访问 OATD，可能需要更换 IP 或调整请求频率。")
                with self.global_lock:
                    self.flush_cookies(self.search_url)
                    time.sleep(2)
                    continue

    def extract_doi(self, text):
        pattern = r'10\.\d{4,9}/[-._;()/:A-Z0-9]+'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(0) if match else None

    def extract_degree_text(self, p_element):
        """
        从 <p class="degree"> 元素中提取直接文本节点内容，去除末尾的 ', '
        """
        if not p_element:
            return ""
        text_parts = []
        for child in p_element.contents:
            if isinstance(child, str):  # 文本节点
                text_parts.append(child)
        combined = ''.join(text_parts)
        # 去除末尾的逗号和空格
        if combined.endswith(', '):
            combined = combined[:-2]
        return combined

    def extract_info_by_list(self, one_paper_tag):
        info_dict = {}
        paper_detail = one_paper_tag.select_one("p.shareIcon > span a")
        if paper_detail and "href" in paper_detail.attrs:
            paper_url = urljoin(base=self.search_url, url=paper_detail.get("href", ""))
            info_dict["paper_url"] = paper_url
        else:
            self.log_print.print(f"error 预料之外情况 实际研究发现 paper_url 应该是有的 原始网站显示也有 : {paper_detail}")
        author_tag = one_paper_tag.select_one("p > span")
        info_dict["Author"] = author_tag.text if author_tag else None
        title_tag = one_paper_tag.select_one("cite.etdTitle > span")
        info_dict["Title"] = title_tag.text if title_tag else None
        degree_tag = one_paper_tag.select_one("p.degree")
        degree_year = self.extract_degree_text(degree_tag).replace("Degree:", "").strip() if degree_tag else None
        if degree_year and "," in degree_year:
            for item in degree_year.split(","):
                item_clean = item.strip()
                if item_clean and item_clean.isdigit():
                    info_dict["Publication Date"] = item_clean
                else:
                    info_dict["Degree"] = item_clean
        else:
            if degree_year and degree_year.isdigit():
                info_dict["Publication Date"] = degree_year
            else:
                info_dict["Degree"] = degree_year
        subject_tag = one_paper_tag.select_one("p.keywords")
        if subject_tag:
            text = subject_tag.get_text(strip=True, separator=" ")
            text = text.replace("Subjects/Keywords:", "").strip()
            info_dict["Subjects/Keywords"] = text
        abstract_tag = one_paper_tag.select_one("div.abstract")
        info_dict["Abstract"] = abstract_tag.text if abstract_tag else None
        publisher_tag = one_paper_tag.select_one("p.degree > a ")
        info_dict["University/Publisher"] = publisher_tag.text if publisher_tag else None
        url_tag = one_paper_tag.select_one("p.links")
        links = url_tag.select("a")
        url_list = []
        for link in links:
            url_list.append(link.get("href"))
            doi = self.extract_doi(link.get("href", ""))
            if doi:
                info_dict["doi"] = doi
        info_dict["url"] = url_list
        try:
            id = info_dict.get("paper_url", "").split("record=")[1].split("&")[0]
            info_dict["_id"] = id
        except Exception as e:
            self.log_print.print(f" id 提取错误 paper_url : {info_dict.get('paper_url','')}  {str(e)}")
            if "doi" in info_dict:
                info_dict["_id"] = generate_doi_id(info_dict.get("doi", ""))
            else:
                info_dict["_id"] = generate_title_id(info_dict.get("Title", ""))
        return info_dict

    def extract_one_page(self, response):
        page_url = response.url
        search_soup = BeautifulSoup(response.text, "html.parser")
        paper_list = search_soup.select("#results > div.result")
        # 虽然丑陋 但是确实是 url 浏览器访问的方式  直接访问详情页的 url 就能拿到信息了
        # 多线程处理详情页
        finished_num = 0
        all_num = len(paper_list)

        # 判断是否有下一页
        # 找到当前页码的 span
        next_page_url = ""
        current_span = search_soup.find('span', class_='this-page')
        if current_span:
            next_page_tag = current_span.find_next_sibling("a")
            href = None
            if next_page_tag:
                href = next_page_tag.attrs.get("href", "")
            if href:
                next_page_url = urljoin(base=self.search_url, url=href)
        current_start = int(page_url.split("=")[-1])
        paging = search_soup.select("p.paging > a")
        matchesReport = search_soup.select_one("p.matchesReport")
        matchesReport_text = matchesReport.get_text(strip=True, separator=' ') if matchesReport else ""
        matchesReport_text = matchesReport_text.replace("\n", " ").strip()
        max_start_result = re.findall(r'(\d+)\s+total matches.', matchesReport_text)
        if max_start_result:
            max_start = int(max_start_result[0])
        else:
            max_start = current_start + 30
        if len(paging) == 0:
            # 只有一页的情况
            next_page_url = ""
            self.log_print.warning(f"只有一页 check 应该是小于30 {matchesReport_text} ")
        if len(paging) == 1:
            self.log_print.error(f"这是不可能的情况呀，数字 加图标icon 至少是两个")
        if len(paging) >= 2:
            max_page_url = paging[-2].attrs.get("href", "=error")
            max_start = int(max_page_url.split("=")[-1])
            current_start = int(page_url.split("=")[-1])
        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_url = {executor.submit(self.extract_info_by_list, one_paper_tag) for one_paper_tag in paper_list}
            result_list = []
            for future in as_completed(future_to_url):
                try:
                    result = future.result()
                    result_list.append(result)
                    finished_num += 1
                    print(f"已完成 {finished_num}/{all_num}  {current_start}/{max_start}， 处理完毕 ：", end="\r")
                except Exception as exc:
                    # https://oatd.org/oatd/record?record=oai\:RDI%20UBA\:archivos\%2Fcartasravi\:me026500-me026500_pdf
                    print(f"error  generated an exception: {exc}")
            self.print_efficiency(result_list)
        return next_page_url, max_start

    def handle_one_page(self, page_url):
        response = self.search_one_page(page_url)
        # 这里应该使用多线程处理
        if not response:
            return "", 0
        next_page_url, max_start = self.extract_one_page(response)
        return next_page_url, max_start

    def handle_one_year(self, year):
        # 假设是 2027-2028 都可以 出版日期  安排出版
        # 当前进度
        has_next_page = True  # 第一次请求默认是有下一页的

        current_start = self.log_current_start.get_int(default=1)
        max_start = current_start + 60
        params = {
            "q": f"*:* AND pub_dt:[{year}-01-01T00:00:00Z TO {year + 1}-01-01T00:00:00Z]",
            "start": current_start,
        }
        page_url = requests.Request('GET', self.search_url, params=params).prepare().url

        self.log_current_start.record_int(current_start)
        page_url, max_start_temp = self.handle_one_page(page_url)
        if max_start_temp:
            max_start = max_start_temp
        self.log_print.print(f"year: {year} current_start: {current_start}  max_start: {max_start} ")
        has_next_page = bool(page_url)
        if has_next_page:
            page_url_list = []
            for start in range(current_start + self.page_size, max_start + self.page_size, self.page_size):
                params = {
                    "q": f"*:* AND pub_dt:[{year}-01-01T00:00:00Z TO {year + 1}-01-01T00:00:00Z]",
                    "start": start,
                }
                page_url = requests.Request('GET', self.search_url, params=params).prepare().url
                page_url_list.append(page_url)
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = {executor.submit(self.handle_one_page, page_url): page_url for page_url in page_url_list}
                for future in as_completed(futures):
                    page_url = futures[future]
                    next_page_url, max_start_temp = future.result()
                    if not next_page_url:
                        if max_start_temp < max_start - 90:
                            self.log_print.error(f"year： {year} page_url: {page_url} 可能存在问题， max_start_temp: {max_start_temp} 远小于 max_start: {max_start} ")
                        else:
                            max_start = max_start_temp + 60
                            current_start = max_start
                            self.log_print.print(f"year： {year}  ，进度 最后一页：  {current_start} / {max_start} ")
                    else:
                        current_start = int(next_page_url.split("=")[-1])
                        current_start = current_start - 30
                        self.log_current_start.record_int(current_start)
                        self.log_print.print(f"year： {year}  ，进度：  {current_start} / {max_start} ")
            self.log_print.print(f"year： {year}  已完成")

    def run(self):
        finished_years = self.log_finished_year.get_list()
        for year in range(self.end_year, self.start_year - 1, -1):
            if str(year) not in finished_years:
                self.flush_cookies(self.search_url)
                self.log_print.print(f"开始爬取 {year} 年的数据")
                self.handle_one_year(year)
                self.log_finished_year.append_to_list(str(year))
                self.log_current_start.clear_value()
            else:
                self.log_print.print(f"{year} 年的数据已爬取，跳过")
        else:
            self.log_print.print(f"all year finished {self.start_year} -- {self.end_year}")


if __name__ == "__main__":
    spider = SpiderOATD()
    spider.run()

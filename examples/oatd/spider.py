"""
Example: oatd — Open Access Theses and Dissertations (oatd.org), list + detail spider.

Demonstrates AsyncRequestHandler for high-concurrency async scraping with aiohttp.
Also shows cookie refresh and proxy rotation patterns.

- AsyncRequestHandler (aiohttp + asyncio.Semaphore): all detail pages of one
  search-result page are fetched in a single fetch_all() batch — the async
  replacement for the classic ThreadPoolExecutor detail-crawl pattern.
- Cookie refresh/renewal: oatd.org sits behind Cloudflare. On 403
  "Just a moment..." responses the spider flushes fresh cookies through a
  headless Chrome (CookieFlush in cookie_flush_playwright_cdp.py). A
  threading.Lock + is_flushing flag guarantees only one thread flushes at a
  time while the others wait.
- Proxy rotation with test validation: pass a real test_url to
  AsyncRequestHandler / SingleRequestHandler and the built-in ProxyUtil
  rotates proxies, validating each candidate against test_url before use.
- list+detail pattern: Solr-style search pages (crawled year by year from
  1800 to the current year) produce paper lists; each paper's record page is
  fetched concurrently, parsed into MongoDB documents and saved as HTML files.
- Redis-backed resume: per-year finished set + current-start cursor.

Run:  python examples/oatd/spider.py
"""

import re
import sys
import time
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
        self.log_print = LogPrint()
        # 日志
        self.db_manager = HandleMongoDB(table_name=self.table_name)
        self.html_saver = handleHTML(pro_name=self.table_name)
        self.cookie_flusher = CookieFlush(port=9223, headless=False)
        self.flush_time = 0
        self.start_time = time.time()
        self.insert_num = 0
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
        self.start_year = 1800
        self.end_year = datetime.now().year
        self.is_flushing = False  # 多线程 防止每个线程都去刷新cookie

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
        if abs(now_time - self.flush_time) < 60 or self.is_flushing:
            while True:  # 如果正在刷新cookie 就等着 别的线程也过来刷新了  反正等会儿就好了
                if not self.is_flushing:
                    break
                else:
                    time.sleep(2)
        else:
            self.is_flushing = True
            cookies = self.cookie_flusher.flush(url)
            print(f"cookie flush : {cookies}" )
            self.cookies.update(cookies)
            self.flush_time = time.time()
            self.is_flushing = False

    # 翻页
    def search_one_page(self, page_url):
        start_time = time.time()
        for retry in range(20):
            try:
                response = self.single_handler.fetch(page_url, headers=self.headers, cookies=self.cookies)
            except Exception as e:
                time.sleep(5)
                continue
            if not response:
                # 请求失败（含 CF 403 挑战页，single_handler 对非200 重试后返回 None）
                with self.global_lock:
                    self.flush_cookies(self.search_url)
                    time.sleep(2)
                    continue
            if response.status_code == 200 and "Search Limiters" in response.text:
                end_time = time.time()
                self.log_print.print(f"search_one_page retry:{retry} ,耗时 {end_time - start_time:.4f}  {page_url}")
                return response
            elif "Server Too Busy - Please slow down" in response.text:
                print(f"需要休息 slow down 默认5s 累积 {retry * 5 }s  {retry }/10")
                time.sleep(5 * retry)
            elif response.status_code == 403 and "Just a moment..." in response.text:
                with self.global_lock:
                    self.flush_cookies(self.search_url)
                    time.sleep(2)
                    continue
            else:  # 待分析
                print("这个一般是 params 问题导致的 异常  bad request ")
                pass
        else:
            self.log_print.error(f"search_oatd 多次尝试  {retry}  后仍无法成功访问 OATD，可能需要更换 IP 或调整请求频率。")
        # raise ValueError(f"search_oatd 多次尝试后仍无法成功访问 OATD，可能需要更换 IP 或调整请求频率。")


    def extract_doi(self, text):
        pattern = r'10\.\d{4,9}/[-._;()/:A-Z0-9]+'
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group(0) if match else None

    def extract_info_by_list(self, one_paper_tag):
        info_dict = {}
        author_tag = one_paper_tag.select_one("p > span")
        info_dict["Author"] = author_tag.text if author_tag else None
        title_tag = one_paper_tag.select_one("cite.etdTitle > span")
        info_dict["Title"] = title_tag.text if title_tag else None
        degree_tag = one_paper_tag.select_one("p.degree ")
        degree_year = degree_tag.text.replace("Degree:", "") if degree_tag else None
        if degree_year and "," in degree_year:
            info_dict["Degree"] = degree_year.split(",")[0] if degree_year else None
            info_dict["Publication Date"] = degree_year.split(",")[1] if degree_year else None
        else:
            info_dict["Degree"] = degree_year
        subject_tag = one_paper_tag.select_one("p.keywords")
        if subject_tag:
            text = subject_tag.get_text(strip=True, separator=" ")
            text = text.replace("Subjects/Keywords:", "").strip()
            info_dict["Subjects/Keywords"] = text
        abstract_tag = one_paper_tag.select_one("div.abstract") or one_paper_tag.select_one("#abstract4")
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
        return info_dict

    def extract_info(self, paper_url, html_text, one_paper_tag):
        info_dict = {"paper_url": paper_url}
        soup = BeautifulSoup(html_text, "html.parser")
        trs = soup.select("table.recordTable > tr")
        # 存在数据缺失的情况 右边完全没有
        # https://oatd.org/oatd/record?record=oai\:RDI%20UBA\:archivos\%2Fcartasravi\:me026500-me026500_pdf
        try:
            for tr in trs:
                tds = tr.select("td")
                if len(tds) == 2:
                    key = tds[0].text
                    if key == "URL":
                        links = tds[1].select("a")
                        url_list = []
                        for link in links:
                            url_list.append(link.get("href"))
                            doi = self.extract_doi(link.get("href", ""))
                            if doi:
                                info_dict["doi"] = doi
                        info_dict["url"] = url_list
                    else:
                        value = tds[1].text
                        info_dict[key] = value
                else:
                    print(f"出现未知情况，应该都是两个 td 的 check by url " + paper_url)
        except Exception as e:
            # one_paper_tag list 里面 提取 标题 url 啥的
            print(f"解析详情页出现异常，尝试从列表页提取信息，check by url " + paper_url)
            info_dict.update(self.extract_info_by_list(one_paper_tag))
        if "Title" not in info_dict:
            info_dict.update(self.extract_info_by_list(one_paper_tag))
        try:
            if "doi" in info_dict:
                info_dict["_id"] = generate_doi_id(info_dict["doi"])
            else:
                info_dict["_id"] = generate_title_id(info_dict["Title"])
        except Exception as e:
            pass
        return info_dict

    def handle_one_paper(self, paper_url, one_paper_tag, html_text):
        start_time = time.time()
        info_dict = self.extract_info(paper_url, html_text, one_paper_tag)
        file_path = self.html_saver.save_html(html=html_text, file_id=info_dict["_id"])
        info_dict["file_path"] = file_path
        end_time = time.time()
        self.log_print.print(f"成功处理论文  耗时{int(end_time - start_time)} s， {info_dict.get('Title', '未知标题')} ")
        return info_dict

    def handle_one_page(self, page_url):
        response = self.search_one_page(page_url)
        if not response:
            return "", 0
        paper_item_list = []
        search_soup = BeautifulSoup(response.text, "html.parser")
        paper_list = search_soup.select("#results > div.result")
        for paper in paper_list:
            paper_detail = paper.select_one("p.shareIcon > span a")
            if paper_detail and "href" in paper_detail.attrs:
                paper_url = urljoin(base=self.search_url, url=paper_detail.get("href", ""))
                paper_item_list.append(
                    {
                        "paper_url": paper_url,
                        "one_paper_tag": paper
                    }
                )
        # 虽然丑陋 但是确实是 url 浏览器访问的方式  直接访问详情页的 url 就能拿到信息了
        # 使用 AsyncRequestHandler 批量并发抓取详情页（aiohttp + Semaphore，内部自带重试与代理轮换）
        all_num = len(paper_item_list)
        async_results = self.async_handler.fetch_all(
            [item["paper_url"] for item in paper_item_list],
            headers=self.headers,
            cookies=self.cookies,
        )
        result_list = []
        finished_num = 0
        for paper_item in paper_item_list:
            paper_url = paper_item["paper_url"]
            html_text = async_results.get(paper_url)
            if not html_text:
                self.log_print.error(f"详情页抓取失败 {paper_url}")
                continue
            info_dict = self.handle_one_paper(paper_url, paper_item["one_paper_tag"], html_text)
            if info_dict:
                result_list.append(info_dict)
            finished_num += 1
            print(f"已完成 {finished_num}/{all_num}， 处理完毕 ", end="\r")
        self.print_efficiency(result_list)

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
        current_start = page_url.split("=")[-1]
        paging = search_soup.select("p.paging > a")
        matchesReport = search_soup.select_one("p.matchesReport")
        matchesReport_text = matchesReport.get_text(strip=True, separator=' ') if matchesReport else ""
        matchesReport_text = matchesReport_text.replace("\n", " ").strip()
        max_start_result = re.findall(r'(\d+)\s+total matches.', matchesReport_text)
        if max_start_result:
            max_start = max_start_result[0]
        else:
            max_start = current_start + 30
        if len(paging) == 0:
            # 只有一页的情况
            next_page_url = ""
            self.log_print.warning(f"只有一页 check 应该是小于30 { matchesReport_text } ")
        if len(paper_item_list) == 1:
            self.log_print.error(f"这是不可能的情况呀，数字 加图标icon 至少是两个")
        if len(paging) >= 2:
            max_page_url = paging[-2].attrs.get("href", "=error")
            max_start = max_page_url.split("=")[-1]
            current_start = page_url.split("=")[-1]

        return next_page_url, max_start

    def handle_one_year(self, year):
        # 假设是 2027-2028 都可以 出版日期  安排出版
        # 当前进度
        has_next_page = True  # 第一次请求默认是有下一页的
        current_start = self.log_current_start.get_int(default=1)
        params = {
            "q": f"*:* AND pub_dt:[{year}-01-01T00:00:00Z TO {year + 1}-01-01T00:00:00Z]",
            "start": current_start,
        }
        page_url = requests.Request('GET', self.search_url, params=params).prepare().url
        while has_next_page:
            current_start = page_url.split("=")[-1]
            self.log_current_start.record_int(current_start)
            page_url, max_start = self.handle_one_page(page_url)
            has_next_page = bool(page_url)

            if has_next_page:
                self.log_print.print(f"year： {year}  ，进度：  {current_start} / {max_start} ")
            else:
                self.log_print.print(f"year： {year}  已完成")

    def run(self):
        finished_years = self.log_finished_year.get_list()
        for year in range(self.start_year, self.end_year + 1):
            if str(year) not in finished_years:
                self.flush_cookies(self.search_url)
                self.log_print.print(f"开始爬取 {year} 年的数据")
                self.handle_one_year(year)
                self.log_finished_year.append_to_list(str(year))
                self.log_current_start.clear_value()
            else:
                self.log_print.print(f"{year} 年的数据已爬取，跳过")


if __name__ == "__main__":
    spider = SpiderOATD()
    spider.run()

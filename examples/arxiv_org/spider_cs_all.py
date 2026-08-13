"""
Example: arxiv_org — arXiv 全部 CS 论文爬虫（高级搜索 + 按 10 天分片）。

从盛大网络 pro1_arxiv_org 迁移。与 spider_arxiv_new.py 互补：
- 后者只抓当天 cs.AI 新增；本文件用高级搜索抓全量 CS 论文（2020 年至今）
- 反爬策略：搜索一次最多返回 10,000 条（50 条/页 × 200 页），
  因此把时间范围切成 10 天一个窗口逐段爬取，单次搜索永不超限
- 列表页 https://arxiv.org/search/advanced?date-from_date=...&date-to_date=...
- 详情解析复用摘要页格式（标题/摘要/作者/分类/日期/DOI）
- PostgreSQL 批量 upsert + Redis 断点（log_date 窗口起点 / log_page 窗口内页码）

arXiv 全量 CS 论文爬虫：10 天时间窗分片 → 高级搜索翻页 → 摘要页详情入库。
"""

import datetime
import json
import re
import time
import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.datetime_utils import convert_date_robust
from jimmyspider.request import CurlRequestHandler, ThreadRequestHandler
from jimmyspider.soup import extractSoup
from jimmyspider.spider import JimmySpider


class SpiderArxivCsAll(JimmySpider):
    def __init__(self, **kwargs):
        kwargs.setdefault("table_name", "article_arxiv_org")  # 与 spider_arxiv_new 同表
        kwargs.setdefault("db_type", "postgresql")
        super().__init__(**kwargs)

        self.site = "https://arxiv.org/"
        self.source = "arxiv"
        self.delete_table_if_less = 20
        self.page_size = 50

        self.log_page = Cache(f"log_page_cs_all_{self.table_name}")
        self.log_date = Cache(f"log_date_cs_all_{self.table_name}")  # 窗口起点，默认 2020-01-01

        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "referer": "https://arxiv.org/search/advanced?advanced=&classification-computer_science=y",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        }
        self.single_handler = CurlRequestHandler(test_url=self.site)
        self.thread_handler = ThreadRequestHandler(
            max_workers=3, test_url=self.site, headers=self.headers
        )
        self.create_table()

    def create_table(self):
        """与 spider_arxiv_new 共用 article_arxiv_org 表，见 spider_arxiv_new.create_table。"""
        create_sql = f'''
          CREATE TABLE IF NOT EXISTS article_arxiv_org (
              id SERIAL PRIMARY KEY,
              article_title VARCHAR(512),
              article_url VARCHAR(512) UNIQUE,
              article_doi VARCHAR(512),
              date_published TIMESTAMP,
              abstract TEXT,
              content TEXT DEFAULT '',
              author_list JSONB,
              category_list JSONB,
              file_info JSONB,
              site VARCHAR(128) DEFAULT '{self.site}',
              source VARCHAR(128) DEFAULT '{self.source}',
              language VARCHAR(16) DEFAULT 'en',
              html TEXT,
              create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
          );
        '''
        is_delete = self.db_manager.drop_table(max_num=self.delete_table_if_less)
        if is_delete:
            self.log_page.clear_value()
        self.db_manager.create_table(create_sql)

    def get_page(self, start_date, end_date, page_num):
        """高级搜索：computer science + 指定日期范围 + 翻页（start 为 0 基偏移）。"""
        page_url = "https://arxiv.org/search/advanced"
        params = {
            "advanced": "",
            "terms-0-operator": "AND",
            "terms-0-term": "",
            "terms-0-field": "title",
            "classification-computer_science": "y",
            "classification-physics_archives": "all",
            "classification-include_cross_list": "include",
            "date-year": "",
            "date-filter_by": "date_range",
            "date-from_date": start_date,
            "date-to_date": end_date,
            "date-date_type": "submitted_date",
            "abstracts": "show",
            "order": "announced_date_first",
            "size": f"{self.page_size}",
            "start": f"{(page_num - 1) * self.page_size}",
        }
        return self.single_handler.fetch(url=page_url, headers=self.headers, params=params)

    def extract_page_articles(self, page_res, start_page):
        """解析高级搜索列表页，返回 (has_next, article_list)。"""
        all_articles = []
        if not page_res or page_res.status_code != 200:
            self.log_print.print(
                f"page {start_page} 页面请求失败，状态码：{page_res.status_code if page_res else '无响应'}"
            )
            return False, all_articles
        soup = BeautifulSoup(page_res.text, "html.parser")
        article_list = soup.select("ol.breathe-horizontal > li.arxiv-result")
        has_next = not soup.select_one("div.paging > span:last-child")
        for article in article_list:
            text_url_dict = {}
            for a in article.select("p.list-title > span > a"):
                if "href" not in a.attrs:
                    continue
                url = urllib.parse.urljoin(self.site, a.attrs["href"])
                text = a.get_text(strip=True)
                text_url_dict[text] = url
            article_url = extractSoup.extract_href(soup=article, selector="p.list-title > a")
            all_articles.append({
                "file_info": json.dumps(text_url_dict, ensure_ascii=False),
                "article_url": article_url,
            })
        return has_next, all_articles

    def parse_article_detail(self, articles):
        """并发抓取摘要页（解析逻辑与 spider_arxiv_new 相同）。"""
        response_dict = self.thread_handler.fetch_all(
            url_list=[a["article_url"] for a in articles],
        )
        for article in articles:
            response_text = response_dict.get(article["article_url"])
            if not response_text:
                self.log_print.print(f"文章 {article['article_url']} 请求失败")
                continue
            article_soup = BeautifulSoup(response_text, "html.parser")

            title_tag = article_soup.select_one("#abs h1.title")
            span_tag = title_tag.select_one("span.descriptor")
            if span_tag:
                span_tag.decompose()
            title_text = title_tag.text.strip()

            abstract_tag = article_soup.select_one("#abs blockquote.abstract")
            span_tag = abstract_tag.select_one("span.descriptor")
            if span_tag:
                span_tag.decompose()
            abstract_text = abstract_tag.text.strip()

            author_info_tags = article_soup.select("#abs div.authors a")
            author_list = {tag.attrs.get("href"): tag.get_text().strip() for tag in author_info_tags}

            subject_tag = article_soup.select_one("td.subjects")
            category_list = [cat.strip() for cat in subject_tag.text.split(";")] if subject_tag else []

            date_tag = article_soup.select_one("div.dateline")
            date_text = date_tag.get_text().strip()
            match = re.search(r"on\s+(.+?)\]", date_text)
            date_published = convert_date_robust(match.group(1).strip()) if match else None

            doi_tag = article_soup.select_one("td.arxivdoi a")
            doi_url = doi_tag.attrs.get("href") if doi_tag else None

            article.update({
                "article_title": title_text,
                "abstract": abstract_text,
                "author_list": json.dumps(author_list, ensure_ascii=False),
                "category_list": json.dumps(category_list, ensure_ascii=False),
                "date_published": date_published,
                "article_doi": doi_url,
                "html": str(article_soup),
                "content": "",
            })

    def handle_ten_days(self, start_date, end_date, start_page):
        """处理一个 10 天时间窗：翻页抓列表 → 抓详情 → upsert。"""
        while True:
            page_res = self.get_page(start_date, end_date, start_page)
            has_next, page_articles = self.extract_page_articles(page_res, start_page)
            if not page_articles:
                self.log_print.info(f"结束 当前page: {start_page} check url")
                break
            self.parse_article_detail(page_articles)
            info = self.db_manager.insert_data_list(page_articles, unique_col="article_url")
            self.log_print.print(f"page {start_page} 入库: {info}")
            if len(page_articles) < self.page_size:
                self.log_print.info(
                    f"结束 当前page: {start_page} 返回数据量 {len(page_articles)} 小于 {self.page_size}"
                )
                break
            if has_next:
                self.log_print.print(f"完成 page {start_page}，准备处理下一页")
                start_page += 1
                self.log_page.record_int(start_page)
                time.sleep(3)
            else:
                self.log_print.info(f"结束 当前page: {start_page} 没有下一页")
                break

    def run(self):
        current_date = self.log_date.get_string(default="2020-01-01")
        ten_days = datetime.timedelta(days=10)
        end_date = datetime.datetime.now()
        start_date = datetime.datetime.strptime(current_date, "%Y-%m-%d")
        while start_date <= end_date:
            start_date_str = start_date.strftime("%Y-%m-%d")
            temp_end = min(start_date + ten_days, end_date)
            end_date_str = temp_end.strftime("%Y-%m-%d")
            self.log_print.info(f"处理时间段: {start_date_str} - {end_date_str}")
            self.handle_ten_days(start_date_str, end_date_str, self.log_page.get_int(default=1))
            self.log_print.info(f"处理时间段 {start_date_str} - {end_date_str} 完成")
            start_date += ten_days
            end_date = datetime.datetime.now()  # 每次循环更新结束时间，防止运行时间过长
            self.log_date.record_string(start_date.strftime("%Y-%m-%d"))


if __name__ == "__main__":
    SpiderArxivCsAll(pro_path=Path(__file__).parent, db_type="postgresql").run()

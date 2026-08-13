"""
Example: arxiv_org — arXiv cs.AI "new" listing crawler (list + detail, PostgreSQL).

从盛大网络 pro1_arxiv_org 迁移。演示：
- 列表页 https://arxiv.org/list/cs.AI/new 分页抓取（?skip=&show=50 每页 50 篇）
- 摘要页详情解析：标题 / 摘要 / 作者 / 分类 / 日期 / DOI
- CurlRequestHandler（curl_cffi TLS 指纹伪装）+ ThreadRequestHandler 并发抓详情
- PostgreSQL 批量 upsert（article_url 唯一键，表数据过少自动重建）
- Redis 断点：log_page 页码 + log_date 日期（跨天自动重置进度）

arXiv cs.AI 最新论文爬虫：抓列表页 → 并发抓每篇摘要页 → 结构化入库。
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
from jimmyspider.spider import JimmySpider


class SpiderArxivNew(JimmySpider):
    def __init__(self, **kwargs):
        kwargs.setdefault("table_name", "article_arxiv_org")
        kwargs.setdefault("db_type", "postgresql")
        super().__init__(**kwargs)

        self.site = "https://arxiv.org/"
        self.source = "arxiv"
        self.delete_table_if_less = 20  # 表数据少于 20 条时重建（首次运行）
        self.page_size = 50

        # 断点缓存：页码 / 日期（跨天自动重置）
        self.log_page = Cache(f"log_page_{self.table_name}_new")
        self.log_date = Cache(f"log_date_{self.table_name}_new")

        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "referer": "https://arxiv.org/list/cs.AI/new",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        }
        self.single_handler = CurlRequestHandler(test_url=self.site)
        self.thread_handler = ThreadRequestHandler(
            max_workers=3, test_url=self.site, headers=self.headers
        )
        self.create_table()

    def create_table(self):
        """建表（article_url 唯一键）；表数据过少则重建并清空页码进度。"""
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
            self.log_print.print("表数据过少已删除，重新建表，清空页码进度")
            self.log_page.clear_value()
        self.db_manager.create_table(create_sql)

    def get_page(self, page_num):
        page_url = "https://arxiv.org/list/cs.AI/new"
        params = {
            "skip": f"{(page_num - 1) * self.page_size}",
            "show": f"{self.page_size}",
        }
        return self.single_handler.fetch(url=page_url, headers=self.headers, params=params)

    def extract_page_articles(self, page_res, start_page):
        """解析列表页，返回 (has_next, article_list, total_entries)。"""
        all_articles = []
        if not page_res or page_res.status_code != 200:
            self.log_print.print(
                f"page {start_page} 页面请求失败，状态码：{page_res.status_code if page_res else '无响应'}"
            )
            return False, all_articles, 0
        soup = BeautifulSoup(page_res.text, "html.parser")
        post_list = soup.select("#articles > dt")
        has_next = not soup.select_one("div.paging > span:last-child")
        page_info = soup.select_one("div.paging").get_text().strip()
        match = re.search(r"Total of\s+([\d,]+)\s+entries", page_info)
        total_entries = int(match.group(1).replace(",", "")) if match else 0

        for article in post_list:
            text_url_dict = {}
            for a in article.select("a"):
                if "href" not in a.attrs:
                    continue
                url = urllib.parse.urljoin(self.site, a.attrs["href"])
                text = a.get_text(strip=True)
                if text.startswith("arXiv"):
                    text_url_dict["abstract"] = url
                else:
                    text_url_dict[text] = url
            article_url = text_url_dict.get("abstract") or text_url_dict.get("html", "")
            all_articles.append({
                "file_info": json.dumps(text_url_dict, ensure_ascii=False),
                "article_url": article_url,
            })
        return has_next, all_articles, total_entries

    def parse_article_detail(self, articles):
        """并发抓取摘要页，解析标题/摘要/作者/分类/日期/DOI。"""
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

    def run(self):
        now_date = datetime.datetime.now().date()
        start_date = self.log_date.get_string()
        if not start_date:
            self.log_print.print("没有日志记录，第一次运行，记录当前时间")
            self.log_page.clear_value()
        else:
            start_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
            if now_date > start_date:
                self.log_print.print(f"新的一天 {now_date}，清除进度重新开始")
                self.log_page.clear_value()
            else:
                self.log_print.print("时间记录异常，跳过本次运行")
                return

        start_page = self.log_page.get_int(default=1)
        while True:
            page_res = self.get_page(start_page)
            has_next, page_articles, total_entries = self.extract_page_articles(page_res, start_page)
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
                self.log_page.clear_value()
                self.log_date.record_string(now_date.strftime("%Y-%m-%d"))
                break
            if has_next:
                self.log_print.print(
                    f"完成 page {start_page}，当前进度 {start_page * self.page_size} / {total_entries}，准备处理下一页"
                )
                start_page += 1
                self.log_page.record_int(start_page)
                time.sleep(30)
            else:
                self.log_print.info(f"结束 当前page: {start_page} 没有下一页")
                self.log_page.clear_value()
                self.log_date.record_string(now_date.strftime("%Y-%m-%d"))
                break

        # 第二阶段（可选）：按标题去 Google Scholar 找作者并抓取作者主页，
        # 见 examples/google_scholar/get_author_by_title.py
        # get_author = GetAuthorByTitle(table_name_read=self.table_name)
        # get_author.run_thread()


if __name__ == "__main__":
    SpiderArxivNew(pro_path=Path(__file__).parent, db_type="postgresql").run()

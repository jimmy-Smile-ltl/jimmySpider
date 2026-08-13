"""
tech_news_flash — 科技快报网新闻抓取（citreport.com）

按文章编号逐条抓取中国科技产业快报新闻（GBK 编码页面），
MySQL 落库 + Redis 断点续爬。迁移自北大信研院 pro1-科技快报网，
去掉了 HDFS 图片上传与硬编码数据库配置。
"""

import random
import time
import warnings
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from jimmyspider import Cache, JimmySpider

warnings.filterwarnings("ignore")


def parse_date(date_str: str):
    """兼容 'YYYY-MM-DD HH:MM:SS' 与 'YYYY-MM-DD' 两种日期格式。"""
    if not date_str:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


class Spider(JimmySpider):
    """citreport.com 科技快报爬虫：按文章编号递增抓取详情页。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("db_type", "mysql")
        kwargs.setdefault("test_url", "https://www.citreport.com")
        kwargs.setdefault("table_name", "tech_news_flash")
        super().__init__(**kwargs)

        self.base_url = "https://www.citreport.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        }
        self.log_page = Cache(f"{self.table_name}_log_page")
        self.error_pages = Cache(f"{self.table_name}_error_pages")
        self.create_table()

    def create_table(self) -> None:
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.table_name}`(
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `article_url` VARCHAR(512) UNIQUE NOT NULL,
            `title` VARCHAR(512),
            `date` DATETIME,
            `categories` JSON,
            `publisher` VARCHAR(256),
            `content` MEDIUMTEXT,
            `image_urls` JSON,
            `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        self.db_manager.create_table(create_sql)

    def fetch(self, url: str):
        """请求页面并强制按 GBK 解码，最多重试 5 次。"""
        for _ in range(5):
            try:
                res = self.single_fetcher.fetch(url, headers=self.headers)
                if res:
                    res.encoding = "gbk"
                    return res
            except Exception as e:
                self.log_print.error(f"请求失败: {e}")
            time.sleep(random.uniform(1, 3))
        return None

    # ---- 页面解析 ----

    def extract_text(self, soup: BeautifulSoup, selector: str) -> str:
        element = soup.select_one(selector)
        return element.text.strip() if element else ""

    def extract_list(self, soup: BeautifulSoup, selector: str) -> list:
        elements = soup.select(selector)
        return [el.text.strip() for el in elements if el.text.strip()]

    def extract_image_urls(self, soup: BeautifulSoup, selector: str) -> list:
        images = soup.select(selector)
        return [img["src"] for img in images if img.get("src", "").startswith("http")]

    def extract_article(self, soup: BeautifulSoup, url: str) -> dict:
        """从详情页提取标题/日期/分类/发布者/正文/图片。"""
        publisher = self.extract_text(soup, "div.contacts > span")
        if ":" in publisher:
            publisher = publisher.split(":")[1].strip()
        return {
            "article_url": url,
            "title": self.extract_text(soup, "h1.ph"),
            "date": parse_date(self.extract_text(soup, "div.cl > p > span")),
            "categories": self.extract_list(soup, "div.portal_sort > ul > li"),
            "publisher": publisher,
            "content": self.extract_text(soup, "#article_content"),
            "image_urls": self.extract_image_urls(soup, "#article_content img"),
        }

    # ---- 主流程 ----

    def handle_one_article(self, page_num: int):
        """抓取单条新闻并入库，返回 (发布日期, 是否成功)。"""
        detail_url = f"{self.base_url}/news/{page_num}-1.html"
        res = self.fetch(detail_url)
        if not res:
            self.log_print.error(f"请求失败，无法获取文章内容: {detail_url}")
            return None, False
        soup = BeautifulSoup(res.text, "html.parser")
        # 页面包含 #messagetext 说明文章不存在或已被删除
        if soup.select_one("#messagetext") is not None:
            self.log_print.info(f"文章不存在或已被删除: {detail_url}")
            return None, False
        article = self.extract_article(soup, detail_url)
        self.save_result(article)  # 按 article_url 唯一键 upsert
        return article["date"], True

    def run(self):
        start_page = self.log_page.get_int()
        self.log_print.info(f"开始爬取 citreport.com，从第 {start_page} 页开始")
        fail_count = 0
        record_date = None  # 最近一次成功抓取的文章日期
        while True:
            start_page += 1
            date, success = self.handle_one_article(start_page)
            if success:
                self.log_page.record_int(start_page)
                fail_count = 0
                record_date = date
            else:
                fail_count += 1
                self.error_pages.append_to_list(start_page)
            # 结束条件：超过历史最大编号，且连续失败 10 次或已追到最近 3 天内的文章
            if start_page > 198327:
                if fail_count >= 10 or (record_date and (datetime.now() - record_date).days < 3):
                    self.log_print.info("连续失败超过 10 次或已追到最新文章，结束爬取")
                    break


if __name__ == "__main__":
    Spider(pro_path=Path(__file__).parent).run()

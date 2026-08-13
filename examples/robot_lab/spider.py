"""
robot_lab — UC Berkeley BAIR 机器人实验室博客抓取（bair.berkeley.edu/blog）

分页抓取博客列表（页码 URL + 下一页按钮判定），并发抓取文章详情，
提取正文/作者/关键词/摘要/图片链接。MySQL 落库 + Redis 断点续爬。
迁移自北大信研院 pro37 机器人 UC Berkeley BAIR。
"""

from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jimmyspider import Cache, JimmySpider
from jimmyspider.datetime_utils import HandleDatetime
from jimmyspider.soup import extractSoup

PROJECT_DIR = Path(__file__).parent


class Spider(JimmySpider):
    """BAIR 博客爬虫：page{N} 分页列表 + 文章详情结构化解析。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("db_type", "mysql")
        kwargs.setdefault("test_url", "https://bair.berkeley.edu/blog")
        kwargs.setdefault("table_name", "robot_lab")
        super().__init__(**kwargs)

        self.site = "https://bair.berkeley.edu/blog"
        self.source = "UC Berkeley BAIR"
        self.category = "机器人"
        self.language = "en"
        self.page_size = 10
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "Referer": "https://bair.berkeley.edu/blog/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
            "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        }
        self.log_page = Cache(f"log_page_{self.table_name}")
        self.create_table()

    def create_table(self) -> None:
        """创建文章表；残留测试数据（<20 条）时先删表并重置断点。"""
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.table_name}`(
            `id` INT AUTO_INCREMENT COMMENT '主键ID',
            `article_title` VARCHAR(512) COMMENT '文章标题',
            `article_url` VARCHAR(512) UNIQUE COMMENT '文章链接',
            `date_published` DATETIME COMMENT '发布日期',
            `abstract` TEXT COMMENT '摘要',
            `content` TEXT COMMENT '正文',
            `author` JSON COMMENT '作者',
            `img_url` JSON COMMENT '图片链接',
            `keywords` JSON COMMENT '关键词',
            `site` VARCHAR(128) COMMENT '网站名称',
            `source` VARCHAR(128) COMMENT '数据来源',
            `language` VARCHAR(16) DEFAULT 'en' COMMENT '语言',
            `html` LONGTEXT COMMENT '文章HTML',
            `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        is_delete = self.db_manager.drop_table(max_num=20)
        if is_delete:
            self.log_page.clear_value()
        self.db_manager.create_table(create_sql)

    # ---- 列表页 ----

    def get_page(self, page_num: int):
        """请求博客列表页：第 1 页无页码后缀，之后为 page{N}。"""
        page_url = self.site if page_num == 1 else f"{self.site}/page{page_num}"
        return self.single_fetcher.fetch(page_url, headers=self.headers)

    def extract_page_articles(self, page_res, start_page: int):
        """解析列表页文章卡片，返回 (是否有下一页, 文章基础信息列表)。"""
        has_next = False
        if not page_res or page_res.status_code != 200:
            self.log_print.print(
                f"page {start_page} 请求失败，状态码："
                f"{page_res.status_code if page_res else '无响应'}"
            )
            return has_next, []
        soup = BeautifulSoup(page_res.text, "html.parser")
        post_list = soup.select("div.posts > div.post")
        next_button = soup.select_one("div.right > a.pagination-item")
        next_disable_button = soup.select_one("div.right > a.pagination-item.disabled")
        if not next_disable_button and next_button:
            has_next = True
        if not post_list:
            self.log_print.print(f"page {start_page} 页面数据为空")
            return has_next, []

        articles = []
        for post in post_list:
            href = extractSoup.extract_href(soup=post, selector="a.post-link")
            abstract_p_list = post.find_all("p", recursive=False)
            abstract = "\n".join(p.get_text(strip=True) for p in abstract_p_list)
            keywords = []
            keywords_tag = post.select_one("meta[name='keywords']")
            if keywords_tag:
                keywords = keywords_tag.attrs.get("content", "").split(",")
            articles.append({
                "article_title": extractSoup.extract_text(soup=post, selector="a.post-link"),
                "article_url": urljoin(self.site, href),
                "author": extractSoup.extract_texts(soup=post, selector="span.post-meta a"),
                "keywords": keywords,
                "date_published": HandleDatetime.convert_date_robust(
                    extractSoup.extract_text(soup=post, selector="span.post-meta:nth-child(2)")
                ),
                "abstract": abstract,
                "site": self.site,
                "source": self.source,
                "language": self.language,
                "img_url": [],
                "content": "",
                "html": "",
            })
        return has_next, articles

    # ---- 详情页 ----

    def parse_article_detail(self, articles):
        """并发抓取文章详情，补齐正文/作者/图片；失败的文章原样入库。"""
        article_insert_list = []
        response_dict = self.async_fetcher.fetch_all(
            url_list=[article["article_url"] for article in articles],
            headers=self.headers,
        )
        for article in articles:
            response_text = response_dict.get(article["article_url"])
            if not response_text:
                self.log_print.print(f"文章 {article['article_url']} 请求失败")
                article_insert_list.append(article)
                continue
            soup = BeautifulSoup(response_text, "html.parser")
            content_div = soup.select_one("article.post-content")
            if not content_div:
                self.log_print.print(f"文章 {article['article_url']} 未找到正文部分")
                article_insert_list.append(article)
                continue
            article["img_url"] = extractSoup.extract_urls_relativeURL(
                soup=content_div, selector="img", relative_url=article["article_url"]
            )
            article["html"] = response_text
            article["content"] = extractSoup.extract_content(content_div)
            article_insert_list.append(article)
        return article_insert_list

    # ---- 主流程 ----

    def run(self):
        start_page = self.log_page.get_int(default=1)
        while True:
            page_res = self.get_page(start_page)
            has_next, page_articles = self.extract_page_articles(page_res, start_page)
            if not page_articles:
                self.log_print.info(f"结束 当前page: {start_page}，无文章数据")
                break
            article_insert_list = self.parse_article_detail(page_articles)
            self.save_result(article_insert_list)  # article_url 唯一键 upsert
            self.log_print.print(f"完成 page {start_page}，插入 {len(article_insert_list)} 条")
            if not has_next:
                self.log_print.info(f"结束 当前page: {start_page}，已是最后一页")
                break
            start_page += 1
            self.log_page.record_int(start_page)


if __name__ == "__main__":
    Spider(pro_path=PROJECT_DIR).run()

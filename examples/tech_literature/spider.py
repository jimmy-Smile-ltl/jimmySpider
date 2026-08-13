"""
tech_literature — Frontiers 科技文献抓取（frontiersin.org）

从期刊列表（约 288 本）出发，逐期刊分页调用文章检索 API，
再并发抓取每篇文章详情页，解析正文/关键词/作者/审稿人/编辑/
参考文献/通讯作者等结构化信息。MySQL 落库 + Redis 断点续爬，
期刊信息会缓存到本地 JSON 避免重复请求。
迁移自北大信研院 pro18 科技文献 Frontiers，去掉了 HDFS 上传。
"""

import base64
import copy
import json
import re
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString

from jimmyspider import Cache, JimmySpider
from jimmyspider.datetime_utils import HandleDatetime
from jimmyspider.soup import extractSoup

PROJECT_DIR = Path(__file__).parent


class Spider(JimmySpider):
    """Frontiers 文献爬虫：期刊列表 -> 文章列表 -> 文章详情三级抓取。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("db_type", "mysql")
        kwargs.setdefault("test_url", "https://www.frontiersin.org/")
        kwargs.setdefault("table_name", "tech_literature")
        super().__init__(**kwargs)

        self.site = "https://www.frontiersin.org/journals"
        self.source = "Frontiers in"
        self.language = "en"
        self.journal_info_file = PROJECT_DIR / "journal_info.json"  # 期刊信息本地缓存

        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "cache-control": "max-age=0",
            "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
        }

        # Redis 断点缓存：当前期刊 / 期刊内页码 / 已抓数量 / 已完成期刊列表
        self.log_journal = Cache(f"log_journal_{self.table_name}")
        self.log_page = Cache(f"log_page_{self.table_name}")
        self.log_finish_num = Cache(f"log_finish_num_{self.table_name}")
        self.log_finished = Cache(f"log_finished_{self.table_name}")

        self.create_table()

    def create_table(self) -> None:
        """创建文章表；残留测试数据（<100 条）时先删表。"""
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS `{self.table_name}`(
            `id` INT AUTO_INCREMENT COMMENT '主键ID',
            `article_id` VARCHAR(64) NOT NULL COMMENT '文章ID',
            `article_title` VARCHAR(512) NOT NULL,
            `article_url` VARCHAR(512) UNIQUE NOT NULL,
            `article_doi` VARCHAR(512) COMMENT 'DOI',
            `article_type` VARCHAR(64) COMMENT '文章类型',
            `keywords` JSON COMMENT '关键词',
            `section` JSON COMMENT '研究领域',
            `date_published` DATETIME COMMENT '发布日期',
            `date_received` DATETIME COMMENT '收到日期',
            `date_accepted` DATETIME COMMENT '录用日期',
            `reviewers` JSON COMMENT '审稿人列表',
            `correspondences` JSON COMMENT '通讯作者',
            `authors` JSON COMMENT '作者列表',
            `editors` JSON COMMENT '编辑列表',
            `abstract` TEXT COMMENT '摘要',
            `content` MEDIUMTEXT COMMENT '正文',
            `references` JSON COMMENT '参考文献',
            `pdf_url` VARCHAR(512) COMMENT 'PDF下载链接',
            `journal_name` VARCHAR(512) NOT NULL COMMENT '期刊名称',
            `journal_url` VARCHAR(512) NOT NULL COMMENT '期刊URL',
            `language` VARCHAR(32) DEFAULT 'en',
            `html` LONGTEXT COMMENT '文章HTML',
            `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (`id`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """
        self.db_manager.drop_table(max_num=100)
        self.db_manager.create_table(create_sql)

    # ==================== 期刊列表 ====================

    def get_journal_infos(self) -> List[dict]:
        """获取全部期刊信息：优先读本地缓存，无缓存则从接口抓取。"""
        if self.journal_info_file.exists():
            with open(self.journal_info_file, "r", encoding="utf-8") as f:
                journal_info_list = json.load(f)
                self.log_print.info(f"从本地文件加载期刊信息，共 {len(journal_info_list)} 条")
                return journal_info_list
        self.log_print.info("本地文件不存在，开始从网站获取期刊信息")
        journal_info_list = self.fetch_journal_infos()
        with open(self.journal_info_file, "w", encoding="utf-8") as f:
            json.dump(journal_info_list, f, ensure_ascii=False, indent=4)
        self.log_print.info(f"已保存期刊信息到 {self.journal_info_file}")
        return journal_info_list

    def fetch_journal_infos(self) -> List[dict]:
        """分页调用期刊检索接口，共约 288 本期刊。"""
        journal_info_list = []
        max_page = 288 // 16 + 2
        for page in range(max_page):
            url = "https://www.frontiersin.org/api/v3/journals/search/journal-filter"
            data = json.dumps({
                "Skip": 16 * page, "Top": 16, "DomainId": 0,
                "JournalIds": [], "Search": "", "FirstLetter": "",
            }, separators=(",", ":"))
            response = self.single_fetcher.fetch(
                url, headers=self.headers, data=data, method="POST"
            )
            if not response:
                continue
            for journal in response.json().get("Journals", []):
                journal_info_list.append({
                    "journal_name": journal.get("AlternativeText", ""),
                    "journal_url": journal.get("PublicUrl", ""),
                    "journal_id": journal.get("Id", ""),
                    "journal_domain_id": journal.get("DomainId", ""),
                    "article_count": journal.get("ArticleCount", {}).get("Count", 0),
                    "article_downloads_count": journal.get("ArticleDownloadsCount", {}).get("Count", 0),
                    "section_count": journal.get("SectionCount", {}).get("Count", 0),
                    "ISSN": journal.get("ISSN", ""),
                })
            self.log_print.info(f"期刊获取进度: {len(journal_info_list)}/288 page {page + 1}")
        return journal_info_list

    # ==================== 单个期刊的文章列表 ====================

    def handle_journal(self, journal_info: dict):
        """处理单个期刊：分页拉取该期刊全部文章并入库。"""
        journal_name = journal_info.get("journal_name")
        journal_url = journal_info.get("journal_url")
        journal_article_count = journal_info.get("article_count", 20)

        # 断点续爬：上次未完成的期刊从断点页码继续，否则从第 0 页开始
        if journal_name == self.log_journal.get_string(default=""):
            page = self.log_page.get_int(default=0)
            self.log_print.info(f"断点续爬，当前期刊: {journal_name} 当前页数 {page}")
        else:
            page = 0

        api_url, journal_id, all_num = self.get_api_url_journal_id(journal_url)
        if not journal_id:
            self.log_print.info(f"无法获取期刊ID，期刊URL: {journal_url}")
            return False
        if journal_article_count == 0:
            journal_article_count = all_num

        self.log_journal.record_string(journal_name)
        finished_sum = self.log_finish_num.get_int(default=0)
        error_count = 0
        while True:
            data = {
                "Skip": page * 16, "Top": 16, "Search": "", "PageNumber": 0,
                "StartDate": "1920/01/01",
                "EndDate": datetime.now().strftime("%Y/%m/%d"),
                "Filter": {
                    "DomainId": 0, "JournalId": journal_id, "SectionId": 0,
                    "VolumeId": 0, "ArticleType": 0, "Sort": 1, "PartOfResearchTopic": 0,
                },
                "ArticleIds": [],
            }
            article_list_res = self.single_fetcher.fetch(
                url=api_url, headers=self.headers, json=data, method="POST"
            )
            if not article_list_res or article_list_res.status_code != 200:
                self.log_print.info(
                    f"无法获取期刊文章列表: {article_list_res.status_code if article_list_res else '无响应'}"
                )
                error_count += 1
                if error_count > 5 or finished_sum > journal_article_count - 100:
                    return True
                continue

            article_list = self.parse_journal_page(article_list_res, journal_info)
            article_list_insert = self.fetch_articles(article_list)
            finished_sum += len(article_list_insert)
            if article_list_insert:
                self.log_print.info(
                    f"期刊 {journal_name} page:{page} 进度 {finished_sum}/{journal_article_count}"
                )
                self.save_result(article_list_insert)  # article_url 唯一键 upsert
                error_count = 0
            else:
                self.log_print.info(f"{journal_name} 没有新数据 {finished_sum}/{journal_article_count}")
                error_count += 1
                if finished_sum > journal_article_count - 100 or error_count > 20:
                    return True
            page += 1
            self.log_page.record_int(page)
            self.log_finish_num.record_int(finished_sum)

    def parse_journal_page(self, response, journal_info: dict) -> List[dict]:
        """解析期刊文章列表接口的返回，输出文章基础信息。"""
        article_list = []
        journal_name = journal_info.get("journal_name")
        journal_url = journal_info.get("journal_url")
        for article in response.json().get("articles", []):
            section_item = article.get("section", {})
            authors = article.get("authors", [])
            article_list.append({
                "article_id": article.get("articleId", 0),
                "article_title": article.get("title", ""),
                "article_url": article.get("publicUrl", ""),
                "article_doi": "https://doi.org/" + article.get("doi", ""),
                "article_type": article.get("articleType", {}).get("name", ""),
                "abstract": article.get("abstract", ""),
                "date_published": HandleDatetime.convert_date_robust(article.get("publishedDate", "")),
                "date_accepted": HandleDatetime.convert_date_robust(article.get("acceptedDate", "")),
                "authors": [author.get("fullName") for author in authors],
                "section": {
                    "section_id": section_item.get("id"),
                    "section_title": section_item.get("title"),
                },
                "journal_name": journal_name,
                "journal_url": journal_url,
                "is_pdf_only": article.get("isArticleArchive", False),
            })
        return article_list

    # ==================== 文章详情 ====================

    def fetch_articles(self, article_list: List[dict]) -> List[dict]:
        """并发抓取文章详情页，补齐正文/关键词/参考文献等字段。"""
        article_list_insert = []
        pdf_url_list = []  # PDF-only 文章直接以列表页链接作为 PDF
        article_url_list = []  # 需要访问正文页的文章
        for article in article_list:
            if article.get("is_pdf_only"):
                pdf_url_list.append(article.get("article_url"))
            else:
                article_url_list.append(article.get("article_url"))

        article_res_dict = self.async_fetcher.fetch_all(
            url_list=article_url_list, headers=self.headers
        )
        for article in article_list:
            pdf_url = None
            if article.pop("is_pdf_only", False):
                pdf_url = article["article_url"]
            article_url = article.get("article_url")
            temp_dict = {
                "content": "", "pdf_url": pdf_url,
                "keywords": [], "references": [], "reviewers": [],
                "correspondences": [], "editors": [],
                "date_received": None, "html": None,
            }
            if article_url in pdf_url_list:
                article.update(temp_dict)
                article_list_insert.append(article.copy())
                continue
            response_text = article_res_dict.get(article_url, None)
            if not response_text:
                self.log_print.info(f"无法访问文章链接 {article_url}，跳过")
                continue

            article_soup = BeautifulSoup(response_text, "html.parser")
            content_tag = article_soup.select_one("div.JournalFullText div.JournalFullText")
            if not content_tag:
                content_tag = article_soup.select_one("#fulltext")
            temp_dict["content"] = extractSoup.extract_content(copy.deepcopy(content_tag))
            temp_dict["pdf_url"] = self.get_download_url(article_soup, article_url)
            temp_dict["keywords"] = self.get_keywords(article_soup, article_url)
            temp_dict["references"] = self.get_references(article_soup, article_url)
            temp_dict["reviewers"] = self.get_reviewer(article_soup, article_url)
            temp_dict["correspondences"] = self.extract_correspondence_info(article_soup, article_url)
            temp_dict["editors"] = self.extract_editors_info(article_soup, article_url)
            temp_dict.update(self.extract_timestamps(article_soup, article_url))
            temp_dict["html"] = response_text
            article.update(temp_dict)
            article_list_insert.append(article.copy())
        return article_list_insert

    def get_api_url_journal_id(self, journal_url: str) -> tuple:
        """访问期刊主页，通过重定向域名与"提交文章"链接解析 API 地址和期刊 ID。"""
        journal_res = self.single_fetcher.fetch(
            url=journal_url, headers=self.headers, method="GET",
            allow_redirects=True, verify=True, retry_count=30,
        )
        api_url_self = "https://www.frontiersin.org/api/v3/journals/search/articles"
        if not journal_res or journal_res.status_code != 200:
            self.log_print.info(f"无法访问期刊链接 {journal_url}")
            return api_url_self, "", 1

        soup = BeautifulSoup(journal_res.text, "html.parser")
        url_tag = soup.select_one("a.Ibar__button.Ibar__submit")
        if not url_tag:
            return "", "", 0
        url = url_tag.get("href")
        if len(url.split("?")) != 2:
            # 兼容非标准页面结构
            url_tag = soup.select_one(
                "ul > li.Accordion__item:nth-child(2) > div.Accordion__content"
                ".Accordion__content--fadeOut > ul > li:first-child > a"
            )
            if not url_tag:
                return "", "", 0
            url = url_tag.get("href")
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        journal_id = params.get("entityid", [None])[0]

        see_all_tag = soup.select_one("section.Home__articles > div.Heading > span > a")
        if see_all_tag:
            all_num = int("".join(re.findall(r"\d", see_all_tag.text)))
        else:
            all_num = 1

        # 按重定向后的域名选择对应 API
        if "frontierspartnerships.org" in journal_res.url:
            return "https://www.frontierspartnerships.org/api/v3/journals/search/articles", journal_id, all_num
        if "frontiersin.org" in journal_res.url:
            return api_url_self, journal_id, all_num
        if ".ebm-journal.org" in journal_res.url:
            return "https://www.ebm-journal.org/api/v3/journals/search/articles", journal_id, all_num
        parsed = urlparse(journal_res.url)
        return f"{parsed.scheme}://{parsed.netloc}/api/v3/journals/search/articles", "", all_num

    # ==================== 详情页字段解析 ====================

    def get_download_url(self, soup, article_url: str):
        """提取 PDF / EPUB 下载链接。"""
        pdf_tag = soup.select_one("ul.ActionsDropDown__menu li a[href$='/pdf']")
        if pdf_tag and pdf_tag.get_text().strip() == "Download PDF":
            return urljoin(article_url, pdf_tag["href"])
        epub_tag = soup.select_one("ul.ActionsDropDown__menu li a[href$='/epub']")
        if epub_tag and epub_tag.get_text().strip() == "EPUB":
            return urljoin(article_url, epub_tag["href"])
        return None

    def get_keywords(self, soup, article_url: str):
        """从 "Keywords:" 标记后的文本节点提取关键词列表。"""
        try:
            whitespace_pattern = re.compile(r"^\s*$")
            keywords_span = soup.find("span", string=re.compile(".{0,5}Keywords.{0,5}"))
            if not keywords_span:
                keywords_span = soup.find("div", string=re.compile(".{0,5}Keywords.{0,5}"))
            keywords_list = []
            if keywords_span:
                keywords_node = keywords_span.next_sibling
                if keywords_node:
                    keywords_string = keywords_node.strip().replace(":", "").strip()
                    keywords_list = [
                        k.strip() for k in keywords_string.split(",")
                        if not whitespace_pattern.match(k)
                    ]
                if not keywords_list:
                    full_text = keywords_span.parent.get_text().replace("Keywords:", "").strip()
                    keywords_list = [
                        k.strip() for k in full_text.split(",")
                        if not whitespace_pattern.match(k)
                    ]
            if not keywords_list:
                self.log_print.info(f"文章 {article_url} 未找到关键词")
            return keywords_list
        except Exception:
            return None

    def get_references(self, soup, article_url: str) -> List[str]:
        """提取参考文献列表（兼容两种页面结构）。"""
        ref_list = []
        references = soup.select("div.JournalFullText div.References > p.ReferencesCopy1")
        for ref in references:
            ref_list.append(ref.get_text(separator=" ").strip())
        if not references:
            references_tag = soup.find("h2", string="References")
            if references_tag:
                next_sibling = references_tag.parent.next_sibling
                while next_sibling:
                    if not isinstance(next_sibling, NavigableString) and next_sibling.select_one("div"):
                        ref_list.append(next_sibling.select_one("div").text.strip())
                    next_sibling = next_sibling.next_sibling
        if not ref_list:
            self.log_print.info(f"文章 {article_url} 未找到参考文献")
        return ref_list

    def get_reviewer(self, soup, article_url: str) -> List[Dict]:
        """提取审稿人列表：[{'name', 'profile_url', 'institution', 'country'}]。"""
        reviewed_by_p = soup.find("p", string="Reviewed by:")
        if not reviewed_by_p:
            return []
        container_div = reviewed_by_p.parent
        if not container_div:
            return []
        reviewer_links = container_div.find_all("a")
        reviewers_list = []
        if not reviewer_links:
            next_node = reviewed_by_p.next_sibling
            while next_node:
                if next_node.name in ["a", "br"]:
                    next_node = next_node.next_sibling
                    continue
                if isinstance(next_node, NavigableString):
                    info_list = next_node.get_text(strip=True).split(",")
                    reviewers_list.append({
                        "name": info_list[0] if len(info_list) > 0 else None,
                        "institution": info_list[1] if len(info_list) > 1 else None,
                        "country": info_list[2] if len(info_list) > 2 else None,
                        "profile_url": None,
                    })
                next_node = next_node.next_sibling
            return reviewers_list

        for link in reviewer_links:
            affiliation_string = ""
            next_node = link.next_sibling
            while next_node and next_node.name not in ["a", "br"]:
                if isinstance(next_node, NavigableString):
                    affiliation_string += next_node.strip()
                next_node = next_node.next_sibling
            affiliation_parts = [p.strip() for p in affiliation_string.strip(", ").split(",")]
            reviewers_list.append({
                "name": link.get_text(strip=True),
                "profile_url": link.get("href", ""),
                "institution": affiliation_parts[0] if affiliation_parts else "",
                "country": affiliation_parts[1] if len(affiliation_parts) >= 2 else "",
            })
        if not reviewers_list:
            self.log_print.info(f"文章 {article_url} 未找到审稿人信息")
        return reviewers_list

    def extract_correspondence_info(self, soup, article_url: str) -> List[Dict]:
        """提取通讯作者（姓名 + 邮箱，兼容 Base64 编码邮箱）。"""
        correspondence_span = soup.find("span", string="*Correspondence:")
        if not correspondence_span:
            correspondence_span = soup.find("div", string="*Correspondence:")
            if not correspondence_span:
                return []
            container_p = correspondence_span.next_sibling
            while isinstance(container_p, NavigableString):
                container_p = container_p.next_sibling
        else:
            container_p = correspondence_span.parent
        if not container_p:
            return []

        correspondence_span.decompose()
        inner_html = "".join(map(str, container_p.contents))
        correspondence_list = []
        for block in inner_html.split(";"):
            if not block.strip():
                continue
            block_soup = BeautifulSoup(block, "html.parser")
            link = block_soup.find("a")
            if not link:
                continue
            email = ""
            base64_email = link.get_text(strip=True)
            if base64_email:
                try:
                    email = base64.b64decode(base64_email).decode("utf-8")
                except Exception:
                    email = ""
            link.decompose()
            name = block_soup.get_text(strip=True).rstrip(",")
            if "," in name:
                name = name.split(",")[0]
            if not email:
                match = re.search(
                    r"[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}",
                    container_p.get_text(strip=True),
                )
                if match:
                    email = match.group(0)
            correspondence_list.append({"name": name, "email": email})
        if not correspondence_list:
            self.log_print.info(f"文章 {article_url} 未找到通讯作者信息")
        return correspondence_list

    def extract_editors_info(self, soup, article_url: str) -> List[Dict]:
        """提取编辑列表（与审稿人解析逻辑一致）。"""
        edited_by_p = soup.find("p", string="Edited by:")
        if not edited_by_p:
            return []
        container_div = edited_by_p.parent
        if not container_div:
            return []
        editor_links = container_div.find_all("a")
        editors_list = []
        if editor_links:
            for link in editor_links:
                affiliation_string = ""
                next_node = link.next_sibling
                while next_node and next_node.name not in ["a", "br"]:
                    if isinstance(next_node, NavigableString):
                        affiliation_string += next_node.strip()
                    next_node = next_node.next_sibling
                affiliation_parts = [p.strip() for p in affiliation_string.strip(", ").split(",")]
                editors_list.append({
                    "name": link.get_text(strip=True),
                    "profile_url": link.get("href", ""),
                    "institution": affiliation_parts[0] if affiliation_parts else "",
                    "country": affiliation_parts[1] if len(affiliation_parts) >= 2 else "",
                })
        else:
            next_node = edited_by_p.next_sibling
            while next_node:
                if next_node.name in ["a", "br"]:
                    next_node = next_node.next_sibling
                    continue
                if isinstance(next_node, NavigableString):
                    info_list = next_node.strip().split(",")
                    if info_list and info_list[0]:
                        editors_list.append({
                            "name": info_list[0],
                            "institution": info_list[1] if len(info_list) > 1 else None,
                            "country": info_list[2] if len(info_list) > 2 else None,
                            "profile_url": None,
                        })
                next_node = next_node.next_sibling
        if not editors_list:
            self.log_print.info(f"文章 {article_url} 未找到编辑信息")
        return editors_list

    def extract_timestamps(self, soup, article_url: str) -> Dict:
        """提取接收 / 录用 / 发布日期（兼容两种页面结构）。"""
        timestamps = {}
        p_timestamps = soup.find("p", id="timestamps")
        if not p_timestamps:
            receive_tag = soup.find("div", class_="metadatekey", string="Received:")
            receive_clean = receive_tag.next_sibling.get_text(strip=True).replace(";", "") if receive_tag else ""
            timestamps["date_received"] = HandleDatetime.convert_date_robust(receive_clean)
            accept_tag = soup.find("div", class_="metadatekey", string="Accepted:")
            accept_clean = accept_tag.next_sibling.get_text(strip=True).replace(";", "") if accept_tag else ""
            timestamps["date_accepted"] = HandleDatetime.convert_date_robust(accept_clean)
            publish_tag = soup.find("div", class_="metadatekey", string=re.compile(r"\s*Published online:\s*"))
            publish_clean = publish_tag.next_sibling.get_text(strip=True).replace(";", "") if publish_tag else ""
            timestamps["date_published"] = HandleDatetime.convert_date_robust(publish_clean)
            return timestamps

        full_text = p_timestamps.get_text(strip=True).replace(";;", ";")
        date_parts = list(filter(None, re.split("[:;：]", full_text)))
        for i, part in enumerate(date_parts):
            try:
                label_clean = part.split(":")[0].strip()
                if i + 1 < len(date_parts):
                    date_clean = date_parts[i + 1].replace(".", "").strip()
                    if "Received" in label_clean:
                        timestamps["date_received"] = HandleDatetime.convert_date_robust(date_clean)
                    elif "Accepted" in label_clean:
                        timestamps["date_accepted"] = HandleDatetime.convert_date_robust(date_clean)
                    elif "Published" in label_clean:
                        timestamps["date_published"] = HandleDatetime.convert_date_robust(date_clean)
            except Exception:
                pass
        if not timestamps:
            self.log_print.info(f"文章 {article_url} 未找到日期信息")
        return timestamps

    # ==================== 主流程 ====================

    def run(self):
        is_delete = self.db_manager.clear_table(max_num=100)  # 测试残留数据清空
        if is_delete:
            self.log_journal.record_string("")
            self.log_page.record_int(0)
            self.log_finished.clear_list(method="trim")
        finished_journals = self.log_finished.get_list(default=[])
        journal_info_list = self.get_journal_infos()
        if not journal_info_list:
            self.log_print.info("没有获取到期刊信息")
            return
        finished_journal_num = len(finished_journals)
        for journal_info in journal_info_list:
            if journal_info.get("journal_name") in finished_journals:
                continue
            self.log_print.info(f"期刊完成进度 {finished_journal_num}/{len(journal_info_list)}")
            finished_journal_num += 1
            is_success = self.handle_journal(journal_info=journal_info)
            if is_success:
                self.log_finished.append_to_list(journal_info.get("journal_name"))
                self.log_journal.record_string("")  # 重置断点
                self.log_page.record_int(0)
                self.log_finish_num.clear_value()
        self.log_print.info("所有期刊处理完成")


if __name__ == "__main__":
    Spider(pro_path=PROJECT_DIR).run()

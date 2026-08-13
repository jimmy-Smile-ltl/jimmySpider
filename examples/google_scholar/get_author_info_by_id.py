"""
Example: google_scholar — 按 scholar_id 抓取 GS 作者主页全部信息。

从盛大网络 pro2_google_scholar 迁移。演示：
- 作者主页 https://scholar.google.com/citations?user=xxx 完整字段解析：
  姓名/头像/主页/学术指标（全部 vs 2020 后）/单位/领域/历年引用/开放获取数
- 文章列表翻页：第一页随首页 HTML 返回，之后 POST json=1 + cstart 翻页
  （每页 100 条，引用数小于 5 的文章之后停止翻页）
- 合作者：view_op=list_colleagues 一次全量获取
- 302 重定向处理：作者 ID 变更时以最终 response.url 中的 user 为准（ID 可换不可失）
- Redis 去重（log_finished_get_info_by_id 集合）+ enforce 参数强制刷新
- extractSoup 只返回相对链接，这里手动 urljoin 补全

按 scholar_id 抓作者主页 → 文章列表翻页 → 合作者 → 结构化作者信息。
"""

import concurrent.futures
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.log_print import LogPrint
from jimmyspider.request import CurlRequestHandler
from jimmyspider.soup import extractSoup


class GetAuthorInfoById:
    def __init__(self):
        # 注意：部分作者主页 302 跳转（如 user=iBAr8iQAAAAJ 跳新 ID）。
        # 方案：id 存请求时传入的 user 参数，跳转后以 response.url 里的新 ID 为准，
        # 两个 ID 都能用；"请进行人机身份验证" 出现频率与 IP 质量强相关，需换代理。
        self.site = "https://scholar.google.com/"
        self.table_name = "author_info_google_scholar"
        log_dir = Path(__file__).resolve().parent / "logs"
        self.log_print = LogPrint(log_dir=log_dir, name="get_author_info_by_id")
        self.log_finished = Cache("log_finished_get_info_by_id")
        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en",
            "cache-control": "max-age=0",
            "priority": "u=0, i",
            "referer": "https://scholar.google.com/scholar?hl=zh-CN&as_sdt=0%2C5&q=Why+and+How+Auxiliary+Tasks+Improve+JEPA+Representations&btnG=",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
        }
        self.cookies = {}
        self.single_handler = CurlRequestHandler(test_url=self.site)  # 测试链接，避免请求过多导致IP被封

    def is_has_record(self, scholar_id):
        """检查是否已经处理过该 ID（Redis 集合去重）。"""
        return self.log_finished.is_member_of_set(scholar_id)

    def get_home_info(self, author_info, scholar_id):
        url = "https://scholar.google.com/citations"
        params = {"user": f"{scholar_id}", "hl": "zh-CN"}
        response = self.single_handler.fetch(url, headers=self.headers, cookies=self.cookies, params=params)
        if not response:
            self.log_print.print(f"ID {scholar_id} 首页请求失败，跳过。数据缺少严重，但仍保存数据库并记录该 ID 已处理")
            return
        soup = BeautifulSoup(response.text, "html.parser")
        # 姓名
        author_info["name"] = extractSoup.extract_text(soup=soup, selector="#gsc_prf_in")
        # 头像（extractSoup 只返回相对链接，手动 urljoin 补全）
        avatar_url = extractSoup.extract_href(soup=soup, selector="#gsc_prf_pua img")
        author_info["avatar_url"] = urljoin(url, avatar_url) if avatar_url else None
        # 主页链接：以最终跳转后的 URL 为准
        author_info["profile_url"] = response.url
        # 情况统计：全部 与 2020 年后 学术指标
        scholar_index = {}
        stats_tag_list = soup.select("#gsc_rsb_st > tbody > tr")
        for stats_tag in stats_tag_list:
            stat_name = (stats_tag.select_one("td.gsc_rsb_sc1").get_text().replace(" ", "").strip()
                         if stats_tag.select_one("td.gsc_rsb_sc1") else "")
            all_std = stats_tag.select("td.gsc_rsb_std")
            all_value = all_std[0].get_text() if all_std else "0"
            after_2020_value = all_std[1].get_text() if len(all_std) > 1 else "0"
            scholar_index[stat_name] = {
                "all": int(all_value) if all_value and all_value.isdigit() else 0,
                "after_2020": int(after_2020_value) if after_2020_value and after_2020_value.isdigit() else 0,
            }
        author_info["scholar_index"] = scholar_index
        # 单位 职称
        affiliation_tag = soup.select_one("#gsc_prf_i > div.gsc_prf_il")
        author_info["affiliation"] = affiliation_tag.get_text() if affiliation_tag else ""
        # 领域（extract_text_urls 返回 {文本: 链接} 字典，值补全为绝对链接）
        category_list = extractSoup.extract_text_urls(soup=soup, selector="#gsc_prf_int a")
        author_info["category"] = {k: urljoin(url, v) for k, v in category_list.items()}
        # 文章（首页自带的列表）
        author_info["article_list"] = self.extract_articles(soup)
        # 每年的引用数量 年份与数量一一对应
        year_tag_list = soup.select("div.gsc_md_hist_b > span.gsc_g_t")
        count_tag_list = soup.select("div.gsc_md_hist_b > a.gsc_g_a")
        cite_per_year = {}
        for year_tag, count_tag in zip(year_tag_list, count_tag_list):
            year = year_tag.get_text()
            count = count_tag.get_text()
            if year and count and count.isdigit():
                cite_per_year[year] = int(count)
        author_info["cite_per_year"] = cite_per_year
        # 开放获取数量 / 非开放获取数量
        open_access_tag = soup.select_one("div.gsc_rsb_m > div.gsc_rsb_m_a")
        non_open_access_tag = soup.select_one("div.gsc_rsb_m > div.gsc_rsb_m_na")
        open_access_num = open_access_tag.get_text().strip().split(" ")[0] if open_access_tag else "0"
        non_open_access_num = non_open_access_tag.get_text().strip().split(" ")[0] if non_open_access_tag else "0"
        author_info["open_access_num"] = int(open_access_num) if open_access_num.isdigit() else 0
        author_info["non_open_access_num"] = int(non_open_access_num) if non_open_access_num.isdigit() else 0
        # 302 跳转时以最终 URL 里的 user 为准
        parsed_url = urlparse(response.url)
        if "user" in parse_qs(parsed_url.query):
            scholar_id = parse_qs(parsed_url.query)["user"][0]
        if response.redirect_count > 0:
            self.log_print.print(f"ID {scholar_id} 发生重定向，最终URL: {response.url}")
        return scholar_id

    def get_coauthors(self, author_info, scholar_id):
        """获取合作者，一次全部不分页（view_op=list_colleagues）。"""
        url = "https://scholar.google.com/citations"
        params = {"view_op": "list_colleagues", "hl": "zh-CN", "json": "", "user": f"{scholar_id}"}
        response = self.single_handler.fetch(url, headers=self.headers, cookies=self.cookies, params=params)
        if not response:
            self.log_print.print(f"ID {scholar_id} 合作者请求失败，跳过。")
            author_info["collaborator_list"] = None
            return
        collaborator_soup = BeautifulSoup(response.text, "html.parser")
        collaborator_list = []
        for collaborator_tag in collaborator_soup.select("div.gsc_ucoar"):
            id = collaborator_tag.attrs.get("id")
            name_tag = collaborator_tag.select_one("h3.gs_ai_name > a")
            name = name_tag.get_text() if name_tag else ""
            profile_url = "https://scholar.google.com" + name_tag.attrs.get("href") if name_tag else ""
            affiliation_tag = collaborator_tag.select_one("div.gs_ai_aff")
            affiliation = affiliation_tag.get_text() if affiliation_tag else ""
            collaborator_list.append({"id": id, "name": name, "profile_url": profile_url, "affiliation": affiliation})
        author_info["collaborator_list"] = collaborator_list

    def get_articles(self, author_info, scholar_id):
        """文章列表翻页：POST json=1 + cstart 翻页，每页 100 条。"""
        article_list = author_info.get("article_list", [])
        page = 0
        page_size = 100
        while True:
            url = "https://scholar.google.com/citations"
            cstart = 20 if page == 0 else page * page_size  # 首页已含前 20 条
            params = {"user": f"{scholar_id}", "hl": "zh-CN", "cstart": f"{cstart}", "pagesize": f"{page_size}"}
            data = {"json": "1"}
            response = self.single_handler.fetch(
                url, headers=self.headers, cookies=self.cookies, params=params, data=data, method="POST"
            )
            if not response:
                self.log_print.print(f"ID {scholar_id} 文章请求失败 当前 cstart: {page * page_size}，跳过。")
                if article_list:
                    author_info["article_list"] = article_list
                return
            res_json = response.json()
            html = res_json.get("B")  # P N 都是 1 不清楚含义
            article_list_more = self.extract_articles(BeautifulSoup(html, "html.parser"))
            article_list.extend(article_list_more)
            if len(article_list_more) < page_size:  # 不足一页了，最后一页
                break
            page += 1
            time.sleep(1)  # 避免请求过快被封IP
        author_info["article_list"] = article_list

    def extract_articles(self, soup_article):
        """解析单页文章表格 tr.gsc_a_tr。"""
        article_list_part = []
        for article_tag in soup_article.select("tr.gsc_a_tr"):
            title_tag = article_tag.select_one("td.gsc_a_t a")
            if not title_tag:
                self.log_print.error(f"== 严重错误 == 文章标题缺失，跳过该文章。手动检查: {str(article_tag)}")
                continue
            title = title_tag.get_text()
            article_url = urljoin(base=self.site, url=title_tag.attrs.get("href"))
            authors_tag = article_tag.select_one("div.gs_gray")
            authors = authors_tag.get_text() if authors_tag else ""  # 纯文字非 URL，有省略号也不处理
            pub_tags = article_tag.select("div.gs_gray")
            publication_info = pub_tags[1].get_text() if len(pub_tags) > 1 else ""
            cited_tag = article_tag.select_one("td.gsc_a_c a")
            cited_num = cited_tag.get_text() if cited_tag else "0"
            year_tag = article_tag.select_one("td.gsc_a_y span")
            year = year_tag.get_text() if year_tag else "0"
            article_list_part.append({
                "article_title": title,
                "article_url": article_url,
                "authors": authors,
                "publication_info": publication_info,
                "cited_num": int(cited_num) if cited_num.isdigit() else 0,
                "year": int(year) if year.isdigit() else 0,
            })
            if cited_num.isdigit() and int(cited_num) < 5:  # 引用数小于5的就不继续翻页了
                break
        return article_list_part

    def check_scholar_id(self, scholar_id):
        """检查 scholar_id 格式是否正确（不应是完整 URL）。"""
        if scholar_id.find("https://") != -1 or scholar_id.find("http://") != -1:
            return False
        return True

    def handle_one_scholar_id(self, scholar_id, enforce=False):
        """处理单个 scholar_id：主页 → 合作者 → 文章列表。已处理过则直接返回 True。"""
        if scholar_id.startswith("https:/"):
            parsed_url = urlparse(scholar_id)
            if "user" in parse_qs(parsed_url.query):
                scholar_id = parse_qs(parsed_url.query)["user"][0]
        if not self.check_scholar_id(scholar_id=scholar_id):
            return False, f"ID {scholar_id} 错误，跳过。"
        if not enforce and self.is_has_record(scholar_id=scholar_id):  # 强制更新则跳过检查
            return True, f"ID {scholar_id} 已处理，跳过。"
        start_time = time.time()
        author_info = {"scholar_id": scholar_id}
        print(f"开始处理 ID {scholar_id} ......")
        scholar_id = self.get_home_info(author_info, scholar_id=scholar_id)
        if scholar_id:
            self.get_coauthors(author_info, scholar_id=scholar_id)
            self.get_articles(author_info, scholar_id=scholar_id)
        self.log_finished.add_to_set(scholar_id)
        end_time = time.time()
        info = f"ID {scholar_id} 处理完成。 耗时 {end_time - start_time:.2f} 秒 文章数 {len(author_info.get('article_list', []))}"
        return author_info, info

    def handle_batch_scholar_id(self, scholar_id_list, enforce=False, max_workers=5):
        """批量处理 scholar_id 列表，多线程并发。"""
        result_list = []
        success_num = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {
                executor.submit(self.handle_one_scholar_id, scholar_id, enforce): scholar_id
                for scholar_id in scholar_id_list
            }
            for future in concurrent.futures.as_completed(future_to_id):
                scholar_id = future_to_id[future]
                try:
                    author_info, info = future.result()
                    if author_info and author_info is not True:
                        result_list.append(author_info)
                        success_num += 1
                    self.log_print.print(f"scholar_id: {scholar_id}, get_author_info {info}")
                except Exception as e:
                    self.log_print.print(f"scholar_id: {scholar_id}, get_author_info 处理异常: {e}")
        info_batch = f"本批次处理完成，共处理 {len(scholar_id_list)} 个ID，成功获取 {success_num} 个作者信息。"
        return result_list, info_batch


if __name__ == "__main__":
    # 示例：单 ID 抓取作者主页全部信息
    getter = GetAuthorInfoById()
    author_info, info = getter.handle_one_scholar_id("DTthB48AAAAJ")
    print(info)
    if author_info and author_info is not True:
        print(json.dumps(author_info, ensure_ascii=False, indent=2))

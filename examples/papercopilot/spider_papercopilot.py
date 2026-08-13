"""
Example: papercopilot — 学术会议论文列表爬虫（AJAX 批量接口逆向 + PostgreSQL）。

从盛大网络 pro3_papercopilot_com 迁移。演示：
- 站点层级：分类 → 会议 → 年份 → 论文，三层遍历 + 多层 Redis 断点
- AJAX 批量接口逆向：论文列表由 JS 的 loadMoreRows 通过
  /wp-admin/admin-ajax.php?action=load_paperlist&batch=N 分批 append 加载，
  每批约 1500 条，直到返回字符串 "0" 结束（batch 0 是默认的 100 条）
- 表头动态对齐解析：th 与 td 一一对应，按表头名分派解析器
  （Title/Authors/Affiliation/Country of Aff./Citation 等，R# 列无意义跳过）
- 单行多字段提取：作者（Google Scholar/主页/DBLP/OpenReview 四类链接 + 机构/国家索引）、
  三种引用数（GS 引用 / 评分平均引用 / 评分字符串引用）
- 会议清单缓存：首次从首页解析三级菜单并存 json，之后读本地缓存
- PostgreSQL 批量 upsert（article_url 唯一键）

Paper Copilot 学术会议论文爬虫：三层遍历会议清单 → 逐年 AJAX 分批抓取 → 入库。
"""

import datetime
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider


class SpiderPaperCopilot(JimmySpider):
    def __init__(self, **kwargs):
        kwargs.setdefault("table_name", "article_paper_copilot")
        kwargs.setdefault("db_type", "postgresql")
        super().__init__(**kwargs)

        self.site = "https://papercopilot.com/"
        self.source = "Paper Copilot"
        self.category = "conference"  # 学术会议

        # 会议清单缓存文件（首次自动生成）
        self.conference_list_file = self.project_root / "papercopilot_conference_list.json"

        # 断点缓存：已完成分类 / 会议 / 年份 + 当前处理中的会议
        self.log_finished_cat = Cache(f"log_finished_cat_{self.table_name}")
        self.log_finished_conf = Cache(f"log_finished_conf_{self.table_name}")
        self.log_finished_year = Cache(f"log_finished_year_{self.table_name}")
        self.log_current_conf = Cache(f"log_current_conf_{self.table_name}")

        self.headers = {
            "accept": "*/*",
            "accept-language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "referer": "https://papercopilot.com/paper-list/ijcai-paper-list/ijcai-2023-paper-list/",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest",
        }
        self.single_handler = SingleRequestHandler(test_url=self.site)
        self.create_table()

    def create_table(self):
        """建表（article_url 唯一键）；表数据过少则重建并清空断点。"""
        create_sql = f'''
          CREATE TABLE IF NOT EXISTS article_paper_copilot (
              id SERIAL PRIMARY KEY,
              article_order VARCHAR(16),
              article_title VARCHAR(512),
              article_url VARCHAR(512) UNIQUE,
              affiliation JSONB,
              authors JSONB,
              cat_name VARCHAR(256),
              citation JSONB,
              conf_name VARCHAR(256),
              country_of_affiliation JSONB,
              session_area VARCHAR(256),
              social_links JSONB,
              status VARCHAR(128),
              year VARCHAR(8),
              year_list_url VARCHAR(512),
              site VARCHAR(128) DEFAULT '{self.site}',
              source VARCHAR(128) DEFAULT '{self.source}',
              language VARCHAR(16) DEFAULT 'en',
              html TEXT,
              create_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              update_time TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
          );
        '''
        is_delete = self.db_manager.drop_table(max_num=20)
        if is_delete:
            self.log_finished_year.clear_value()
            self.log_finished_conf.clear_value()
            self.log_finished_cat.clear_value()
            self.log_current_conf.clear_value()
        self.db_manager.create_table(create_sql)

    # ── 会议清单 ─────────────────────────────────────────────────────
    def load_paper_copilot_conference_list(self):
        """读本地缓存；没有则从首页解析三级菜单（分类 → 会议 → 年份）。"""
        if os.path.exists(self.conference_list_file):
            with open(self.conference_list_file, "r", encoding="utf-8") as f:
                conference_list = json.load(f)
            self.log_print.print(f"从缓存加载会议清单 {self.conference_list_file}，共 {len(conference_list)} 个分类")
            return conference_list
        conference_list = self.load_paper_copilot_category_list_by_requests()
        with open(self.conference_list_file, "w", encoding="utf-8") as f:
            json.dump(conference_list, f, ensure_ascii=False, indent=4)
        self.log_print.print(f"已生成并保存会议清单，共 {len(conference_list)} 个分类")
        return conference_list

    def load_paper_copilot_category_list_by_requests(self):
        response = self.single_handler.fetch(self.site, headers=self.headers)
        if not response:
            raise ValueError("首页请求失败，会议列表生成失败，无法继续")
        soup = BeautifulSoup(response.text, "html.parser")
        data_list = []
        trs = soup.select("div.intro-shortcode > table:nth-child(7) tbody tr")
        for tr in trs:
            cat_a = tr.select_one("td:nth-child(1) a")
            cat_name = cat_a.get_text().strip()
            cat_stat_url = urljoin(self.site, cat_a.get("href", "")) if cat_a else ""
            sub_list = []
            for sub in tr.select("td:nth-child(2) > ul.top-menu > li.menu-item-has-children"):
                sub_a = sub.select_one("a")
                sub_name = sub_a.get_text().strip()
                sub_url = urljoin(self.site, sub_a.get("href", "")) if sub_a else ""
                year_url_list = []
                for per_year in sub.select("ul.sub-menu > li.menu-item-has-children"):
                    year_name = per_year.select_one("a span.nav-menu-item-inside").get_text().strip()
                    if not year_name.isdigit():
                        continue
                    type_url_tag = per_year.find("a", string=lambda text: text and "Paper List" in text)
                    if not type_url_tag:  # 放弃还没有 paper list 的年份（征稿阶段）
                        continue
                    year_url_list.append({
                        "year_name": year_name,
                        "type_text": type_url_tag.get_text().strip(),
                        "type_url": urljoin(self.site, type_url_tag.get("href", "").strip()),
                    })
                sub_list.append({"conf_name": sub_name, "conf_url": sub_url, "year_url_list": year_url_list})
            data_list.append({"cat_name": cat_name, "cat_stat_url": cat_stat_url, "conf_list": sub_list})
        return data_list

    # ── 单年论文列表 ──────────────────────────────────────────────────
    def handle_one_year(self, cat, conf, year_info):
        """处理某一年：先访问年份页拿 ajaxmeta 参数，再分批调 AJAX 直到返回 "0"。"""
        year_url = year_info["type_url"]
        if not year_url:
            self.log_print.error(f"年份信息缺少 type_url: {year_info}")
            return
        ajaxmeta, th_titles = self.visit_home_year(year_url)
        if not ajaxmeta or not th_titles:
            self.log_print.error(f"年份页解析失败，拿不到 ajaxmeta 或表头: {year_info}")
            return
        all_papers = []
        ajax_url = ajaxmeta.get("ajax_url", "https://papercopilot.com/wp-admin/admin-ajax.php")
        batch = 0
        initial_dict = {
            "cat_name": cat["cat_name"],
            "conf_name": conf["conf_name"],
            "year": year_info["year_name"],
            "year_list_url": year_info["type_url"],
        }
        while True:
            params = {
                "action": "load_paperlist",
                "batch": batch,  # 从 0 开始累加，直到返回字符串 "0"
                "conf": ajaxmeta.get("conf", ""),
                "year": ajaxmeta.get("year", ""),
                "mode": ajaxmeta.get("mode", ""),
                "track": ajaxmeta.get("track", ""),
            }
            # check_size/check_status_code=False：结束标记 "0" 只有 1 字节，不能被默认校验吞掉
            response = self.single_handler.fetch(
                ajax_url, params=params, check_size=False, check_status_code=False
            )
            if not response:
                self.log_print.error(f"请求失败: {ajax_url} params: {params}")
                break
            if response.status_code != 200:
                self.log_print.error(f"状态码异常: {response.status_code} {ajax_url}")
                break
            if response.text.strip() == "0":
                self.log_print.print(f"所有论文获取完毕，共 {len(all_papers)} 篇")
                break
            batch += 1
            res_text = (response.text
                        .replace("\\/", "/")
                        .replace("&lt;", "<").replace("&gt;", ">")
                        .replace("&quot;", "'").replace("&amp;", "&").replace('\\"', '"'))
            batch_papers = self.parse_paper_list_html(res_text, th_titles, initial_dict)
            self.log_print.print(
                f"第 {batch} 批 {len(batch_papers)} 篇，当前总数 {len(all_papers) + len(batch_papers)}"
            )
            if not batch_papers:
                self.log_print.error(f"本批解析不到任何论文，终止: {ajax_url} params: {params}")
                break
            all_papers.extend(batch_papers)
        self.db_manager.insert_data_list(all_papers, unique_col="article_url")

    def visit_home_year(self, year_url):
        """年份页中提取 var ajaxmeta = {...} 和二级表头 th_titles。"""
        response = self.single_handler.fetch(year_url, headers=self.headers)
        if not response:
            self.log_print.error(f"年份页请求失败: {year_url}")
            return None, None
        year_soup = BeautifulSoup(response.text, "html.parser")
        ajaxmeta_tag = year_soup.find(
            "script", string=lambda text: text and "var ajaxmeta" in text,
            id="paperlist.ajax-ts-extra",
        )
        if not ajaxmeta_tag:
            self.log_print.error("未找到含 ajaxmeta 的 script 标签 (id=paperlist.ajax-ts-extra)")
            return None, None
        m = re.search(r"var\s+ajaxmeta\s+=\s+({.*?});", ajaxmeta_tag.get_text())
        if not m:
            self.log_print.error("ajaxmeta json 解析失败")
            return None, None
        ajaxmeta = json.loads(m.group(1))
        thead = year_soup.select_one("#paperlist thead tr:nth-child(2)")
        th_titles = []
        if thead:
            aff_switch = thead.select_one("#aff_switch")
            if aff_switch is not None:
                aff_switch.decompose()
            th_titles = [th_tag.get_text().strip() for th_tag in thead.find_all("th")]
        return ajaxmeta, th_titles

    # ── 单行解析 ─────────────────────────────────────────────────────
    def parse_paper_list_html(self, res_text, th_titles, initial_dict):
        paper_list = []
        for tr_text in re.findall(r"(<tr>.*?</tr>)", res_text, re.S):
            tr_soup = BeautifulSoup(tr_text, "html.parser")
            one_paper_info = self.handle_one_tr(th_titles, tr_soup)
            if not one_paper_info:
                self.log_print.error(f"解析失败: {tr_text[:200]}")
                continue
            one_paper_info.update(initial_dict)
            one_paper_info["html"] = str(tr_soup)
            paper_list.append(one_paper_info)
        return paper_list

    def handle_one_tr(self, th_titles, tr_soup):
        """表头与 td 一一对应，按表头名分派解析器。"""
        td_tags = tr_soup.find_all("td")
        if len(th_titles) != len(td_tags):
            self.log_print.error(f"表头和数据列数量不匹配: {len(th_titles)} vs {len(td_tags)}")
            return None
        one_paper_info = {}
        for th_title, td in zip(th_titles, td_tags):
            title = th_title.strip()
            if title == "Title":
                article_a = td.select_one("a")
                if article_a:
                    one_paper_info["article_title"] = article_a.get_text().strip()
                    one_paper_info["article_url"] = article_a.get("href", "").strip().replace('\\"', "")
                    one_paper_info["social_links"] = {
                        link.get("title", "").strip().lower().replace('\\"', ""):
                            link.get("href", "").replace('\\"', "").strip()
                        for link in td.select("ul li a") if link.get("href", "").strip()
                    }
                continue
            if title == "Authors":
                one_paper_info["authors"] = self.handle_td_author(td)
                continue
            if title == "Affiliation":
                one_paper_info["affiliation"] = self.handle_td_affiliation(td)
                continue
            if "Country" in title:
                one_paper_info["country_of_affiliation"] = self.handle_td_country(td)
                continue
            if "Citation" in title:  # 'Citation' / 'CitationRating Avg.Ratings' 等
                one_paper_info["citation"] = self.handle_td_cite(td)
                continue
            clean = title.lower().replace(" ", "_").replace("/", "_")
            if clean == "#":
                clean = "article_order"
            if clean == "r#":  # 排序号列无意义，跳过
                continue
            one_paper_info[clean] = td.get_text().strip()
        return one_paper_info

    def handle_td_author(self, td):
        author_list = []
        for author_span in td.select("span"):
            author_list.append({
                "author_name": author_span.get_text().strip(),
                "author_url_googlescholar": author_span.get("data-gs", "").strip().replace('\\"', ""),
                "author_url_hp": author_span.get("data-hp", "").strip().replace('\\"', ""),
                "author_url_dblp": author_span.get("data-dblp", "").strip().replace('\\"', ""),
                "author_url_or": author_span.get("data-or", "").strip().replace('\\"', ""),
                "author_country_idx": author_span.get("data-country", "").strip(),  # 对应 Country of Aff. 索引
                "author_aff_idx": author_span.get("data-aff", "").strip(),          # 对应 Affiliation 索引
            })
        return author_list

    def handle_td_cite(self, td):
        citation_span = td.select_one("span")
        if not citation_span:
            return {}
        cite_dict = {}
        for key, attr in (("citation_gs", "data-gs"), ("rating_avg", "data-rating_avg"),
                          ("rating_str", "data-rating_str")):
            html_str = citation_span.get(attr, "").strip().replace('\\"', "")
            cite_soup = BeautifulSoup(html_str, "html.parser")
            cite_dict[key] = {
                "cite_num": cite_soup.get_text(strip=True) or "0",
                "cite_url": cite_soup.a["href"] if cite_soup and cite_soup.a else "",
            }
        return cite_dict

    def handle_td_affiliation(self, td):
        return [
            {"aff_name": aff_span.get_text().strip(), "aff_url": aff_span.get("href", "").strip().replace('\\"', "")}
            for aff_span in td.select("span a")
        ]

    def handle_td_country(self, td):
        return [span.get_text().strip() for span in td.select("span") if span.get_text().strip()]

    # ── 主流程 ───────────────────────────────────────────────────────
    def run(self):
        finished_cats = self.log_finished_cat.get_list()
        finished_confs = self.log_finished_conf.get_list()
        finished_years = self.log_finished_year.get_list()
        current_conf = self.log_current_conf.get_string()
        conference_list = self.load_paper_copilot_conference_list()

        for cat in conference_list:
            if cat["cat_name"] in finished_cats:
                self.log_print.print(f"已完成分类 {cat['cat_name']}")
                continue
            for conf in cat["conf_list"]:
                if conf["conf_name"] in finished_confs:
                    self.log_print.print(f"已完成会议 {conf['conf_name']}")
                    continue
                self.log_current_conf.record_string(conf["conf_name"])
                self.log_print.print(f"开始处理会议 {conf['conf_name']}  url: {conf['conf_url']}")
                for year_info in conf["year_url_list"]:
                    if year_info["year_name"] in finished_years:
                        continue
                    year_id = f"{cat['cat_name']} --> {conf['conf_name']} --> {year_info['year_name']}"
                    self.log_print.print(f"开始处理年份 {year_id}  url: {year_info['type_url']}")
                    self.handle_one_year(cat, conf, year_info)
                    self.log_finished_year.append_to_list(year_info["year_name"])
                    time.sleep(5)
                self.log_finished_conf.append_to_list(conf["conf_name"])
                self.log_finished_year.clear_value()  # 不同会议年份可能相同，会议完成即清空
                self.log_current_conf.clear_value()
                time.sleep(30)
            self.log_finished_cat.append_to_list(cat["cat_name"])
            self.log_finished_conf.clear_value()
            time.sleep(60)
        self.log_print.print("所有分类会议论文抓取完毕")


if __name__ == "__main__":
    SpiderPaperCopilot(pro_path=Path(__file__).parent, db_type="postgresql").run()

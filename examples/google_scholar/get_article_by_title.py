"""
Example: google_scholar — 按标题搜索 GS 论文并解析作者信息。

从盛大网络 pro2_google_scholar 迁移（原文件名 get_artilce_by_title.py，
方法名 handle_onn_title 修正为 handle_one_title）。演示：
- 搜索接口 https://scholar.google.com/scholar?hl=zh-CN&q=... 结果列表解析
  （#gs_res_ccl_mid > div.gs_r.gs_or.gs_scl）
- 单结果多字段提取：标题 / 链接 / 被引用次数 / 发布信息 + 作者列表
  （部分作者有 GS 主页 URL，部分没有，按有无分开处理）
- CurlRequestHandler（curl_cffi TLS 指纹伪装）访问 Google Scholar
- 返回 (article_list, info) 二元组，供上层 get_author_by_title.py 调用

按标题搜索 Google Scholar → 解析结果列表 → 提取文章与作者信息。
"""

import re
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from jimmyspider.log_print import LogPrint
from jimmyspider.request import CurlRequestHandler


class GetArticleByTitle:
    def __init__(self):
        self.site = "https://scholar.google.com/"
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
        log_dir = Path(__file__).resolve().parent / "logs"
        self.log_print = LogPrint(log_dir=log_dir, name="Get_Article_By_Title")

    def handle_one_title(self, title):
        """按标题搜索，返回 (article_list, info)。"""
        self.title = title
        start_time = time.time()
        url = "https://scholar.google.com/scholar"
        params = {
            "hl": "zh-CN",
            "q": f"{self.title}",
            "btnG": "",
            "as_sdt": "0,5",
        }
        response = self.single_handler.fetch(url, headers=self.headers, cookies=self.cookies, params=params)
        if not response:
            return None, f"请求失败: {url}"
        soup = BeautifulSoup(response.text, "html.parser")
        result_list = soup.select("#gs_res_ccl_mid > div.gs_r.gs_or.gs_scl")
        if not result_list:
            return None, f"未找到结果: {url} len(result_list)={len(result_list)}"
        article_list = []
        for result_item in result_list:
            article_info = {}
            title_tag = result_item.select_one("h3.gs_rt")
            article_info["article_title"] = title_tag.get_text()
            article_info["article_url"] = title_tag.a["href"] if title_tag.a else None
            cite_tag = result_item.select_one("div.gs_ri > div.gs_fl.gs_flb > a:nth-child(3)")
            if cite_tag and "被引用次数" in cite_tag.get_text():
                article_info["cited_num"] = int(re.search(r"被引用次数：(\d+)", cite_tag.get_text()).group(1))
            else:
                article_info["cited_num"] = None
            article_info["html"] = str(result_item)
            self.extract_author_info(result_item, url, article_info)
            article_list.append(article_info)
        end_time = time.time()
        info = f"得到文章{len(article_list)} 用时{end_time - start_time:.2f}秒"
        return article_list, info

    def extract_author_info(self, result_item, url, article_info):
        """解析作者行 div.gs_a：先取发布信息，再取有 URL 的作者，最后补无 URL 的作者。"""
        author_tag_p = result_item.select_one("div.gs_a.gs_fma_p")
        if not author_tag_p:
            author_tag = result_item.select_one("div.gs_a")
            publish_info = author_tag.get_text().replace("\xa0", " ").strip().split("-")[-1].strip()
            article_info["publish_info"] = publish_info
        else:
            author_tag = author_tag_p.select_one("div.gs_fmaa")
        author_dict = {}
        for link in author_tag.select("a"):
            name = link.get_text().strip()
            href = link.get("href")
            url = urljoin(url, href)
            author_dict[name] = url
        # 有些有 url 有些没有 url，所以要分开处理
        author_list = [item.strip() for item in author_tag.get_text().replace("\xa0", " ").split(",")]
        author_dict_list = []
        for idx, name in enumerate(author_list):
            author_dict_list.append({"name": name, "order": idx + 1, "url": author_dict.get(name, None)})
        article_info["author_dict_list"] = author_dict_list.copy()
        if "publish_info" in article_info:
            return
        author_tag.decompose()
        author_tag_p = result_item.select_one("div.gs_a.gs_fma_p")
        if not author_tag_p:
            author_tag_p = result_item.select_one("div.gs_a")
            if author_tag_p:
                publish_info = author_tag_p.get_text().replace("\xa0", " ").strip()
                article_info["publish_info"] = publish_info


if __name__ == "__main__":
    get_article = GetArticleByTitle()
    for title in [
        "Why and How Auxiliary Tasks Improve JEPA Representations",
        "Trajectory Flow Matching with Applications to Clinical Time Series Modelling",
    ]:
        article_list, info = get_article.handle_one_title(title)
        print(f"title: {title} -> {info}")
        print(article_list)

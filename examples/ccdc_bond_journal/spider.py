"""
ccdc_bond_journal/spider.py
───────────────────────────
示例：中央结算公司《债券》期刊文章抓取
(https://www.ccdc.com.cn/Fmi/Thinktank/Article/) —— 中国债券市场金融期刊，
由本地 JSON 期次清单驱动，逐期解析文章列表。

演示内容：
  1. 本地 JSON 驱动采集：issues.json 保存「年份 → 各期次标题与 URL」清单，
     主流程按期次顺序抓取（无需爬期刊列表页）
  2. 期刊页结构解析：div.journal_qlist 分类块（div.journal_stitle 为分类名），
     每期 li 提取 标题 / 作者 / docPubUrl / docId / curPage
  3. 断点续爬：log_issue 记录 issue_idx 进度，中断后从该期次恢复
  4. 错误重试：error_issue_set 记录失败期次索引，主流程后最多 3 轮重试
  5. _id 降级：文档 ID / 文档 URL / 标题 依次兜底生成唯一 ID

数据字段：年份、期刊、期刊URL、分类、标题、作者、url、文档ID、页面路径。
"""

import json
import time
import datetime
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from jimmyspider.cache import Cache
from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    def __init__(self, *args, **kwargs):
        # 自动定位项目目录（等价于入口处传 pro_path=Path(__file__).parent）
        kwargs.setdefault("pro_path", Path(__file__).parent)
        super(Spider, self).__init__(*args, **kwargs)
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)

        self.base_url = "https://www.ccdc.com.cn"
        self.index_url = "https://www.ccdc.com.cn/Fmi/Thinktank/Article/"
        self.issues_path = Path(__file__).parent / "issues.json"

        self.headers = {
            "Accept": "text/html, */*; q=0.01",
            "Accept-Language": "en,en-CN;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Referer": self.index_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }

        self.log_issue = Cache(f"{self.table_name}_log_issue")
        self.error_issue_set = Cache(f"{self.table_name}_error_issue_set")

    # ------------------------------------------------------------------ #
    #  Cache helpers                                                       #
    # ------------------------------------------------------------------ #

    def _encode_cache(self, value: Dict) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)

    def _decode_cache(self, value: str) -> Dict:
        return json.loads(value)

    # ------------------------------------------------------------------ #
    #  Issue list                                                          #
    # ------------------------------------------------------------------ #

    def load_issues(self) -> List[Dict]:
        if not self.issues_path.exists():
            self.log_print.error(f"issues.json not found: {self.issues_path}")
            return []
        with open(self.issues_path, "r", encoding="utf-8") as file:
            try:
                return json.load(file)
            except Exception as exc:
                self.log_print.error(f"issues.json decode error: {exc}")
                return []

    @staticmethod
    def flatten_issues(issue_tree: List[Dict]) -> List[Dict]:
        results = []
        for year_item in issue_tree:
            year = year_item.get("year")
            for issue in year_item.get("issues", []):
                results.append({
                    "year": year,
                    "issue_title": issue.get("issue_title", ""),
                    "issue_url": issue.get("issue_url", ""),
                })
        return results

    # ------------------------------------------------------------------ #
    #  Issue page parsing                                                  #
    # ------------------------------------------------------------------ #

    def get_issue_page(self, issue_url: str) -> Optional[str]:
        response = self.single_fetcher.fetch(
            issue_url,
            headers=self.headers,
            method="GET",
            check_size=False,
        )
        if response and response.status_code == 200:
            response.encoding = response.apparent_encoding
            return response.text
        return None

    def parse_issue_page(self, html_text: str, issue_info: Dict) -> List[Dict]:
        soup = BeautifulSoup(html_text, "html.parser")
        now_ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        results = []
        for journal in soup.find_all("div", class_="journal_qlist"):
            title_tag = journal.find("div", class_="journal_stitle")
            category = title_tag.get_text(strip=True) if title_tag else ""

            for li in journal.find_all("li"):
                info_tag = li.find("a", class_="journal_info")
                title = info_tag.get_text(strip=True) if info_tag else ""

                writer_div = li.find("div", class_="writerList")
                if writer_div:
                    author_span = writer_div.find("span")
                    author = author_span.get_text(strip=True).replace("作者:", "").strip() if author_span else ""
                else:
                    author = ""

                url_div = li.find("div", attrs={"name": "docPubUrl"})
                doc_url = url_div.get_text(strip=True) if url_div else ""

                docid_div = li.find("div", attrs={"name": "docId"})
                doc_id = docid_div.get_text(strip=True) if docid_div else ""

                page_div = li.find("div", attrs={"name": "curPage"})
                cur_page = page_div.get_text(strip=True) if page_div else ""

                unique_key = doc_id or doc_url or title
                results.append({
                    "_id": generate_string_id(unique_key),
                    "年份": issue_info.get("year", ""),
                    "期刊": issue_info.get("issue_title", ""),
                    "期刊URL": issue_info.get("issue_url", ""),
                    "分类": category,
                    "标题": title,
                    "作者": author,
                    "url": doc_url,
                    "文档ID": doc_id,
                    "页面路径": cur_page,
                    "create_time": now_ts,
                })

        return results

    # ------------------------------------------------------------------ #
    #  Retry error issues                                                   #
    # ------------------------------------------------------------------ #

    def handle_error_issue(self, issue_list: List[Dict]) -> bool:
        error_keys = list(self.error_issue_set.get_set_members())
        if not error_keys:
            self.log_print.print("handle_error_issue: 无 issue 需要处理")
            return True

        for error_key in error_keys:
            issue_info = self._decode_cache(error_key)
            issue_idx = issue_info.get("issue_idx")
            if issue_idx is None or issue_idx >= len(issue_list):
                self.error_issue_set.remove_from_set(error_key)
                continue

            issue = issue_list[issue_idx]
            html_text = self.get_issue_page(issue.get("issue_url", ""))
            if html_text:
                data_list = self.parse_issue_page(html_text, issue)
                if data_list:
                    self.save_result(insert_list=data_list)
                self.error_issue_set.remove_from_set(error_key)
            else:
                self.log_print.print(f"handle_error_issue idx:{issue_idx} 采集失败")
                return False

        return len(self.error_issue_set.get_set_members()) == 0

    # ------------------------------------------------------------------ #
    #  Main entry                                                          #
    # ------------------------------------------------------------------ #

    def run_all(self):
        issue_tree = self.load_issues()
        issue_list = self.flatten_issues(issue_tree)
        if not issue_list:
            self.log_print.error("issue_list 为空，终止")
            return

        progress_str = self.log_issue.get_string(default="")
        if progress_str:
            progress = json.loads(progress_str)
            start_idx = progress.get("issue_idx", 0)
        else:
            start_idx = 0

        self.log_print.print(
            f"开始抓取 中央结算公司《债券》期刊, 恢复自 issue_idx:{start_idx}..."
        )

        for issue_idx in range(start_idx, len(issue_list)):
            issue = issue_list[issue_idx]
            issue_url = issue.get("issue_url", "")
            issue_title = issue.get("issue_title", "")
            year = issue.get("year", "")

            self.log_print.print(f"开始抓取 {year} - {issue_title}")
            html_text = self.get_issue_page(issue_url)
            if html_text:
                data_list = self.parse_issue_page(html_text, issue)
                if data_list:
                    self.save_result(insert_list=data_list)
                    self.log_print.print(
                        f"  [{year}-{issue_title}] 采集成功 {len(data_list)} 条"
                    )
                else:
                    self.log_print.warning(
                        f"  [{year}-{issue_title}] 解析无数据"
                    )
            else:
                self.log_print.print(
                    f"  [{year}-{issue_title}] 请求失败，记录错误"
                )
                self.error_issue_set.add_to_set(self._encode_cache({"issue_idx": issue_idx}))

            self.log_issue.record_string(json.dumps({"issue_idx": issue_idx + 1}))
            time.sleep(1)

        self.log_print.print("主流程采集完成")
        self.log_issue.clear_value()

        for retry in range(3):
            self.log_print.warning(f"开始处理错误 issue (第 {retry + 1} 次)")
            if self.handle_error_issue(issue_list):
                break


if "__main__" == __name__:
    spider = Spider()
    spider.run_all()

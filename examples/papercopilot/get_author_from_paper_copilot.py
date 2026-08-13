"""
Example: papercopilot — 论文作者信息流水线（读取 PG 表 + 跨示例调用 google_scholar）。

从盛大网络 pro3_papercopilot_com 迁移。演示：
- 管道型脚本：读取 spider_papercopilot.py 入库的 article_paper_copilot 表，
  按 id 批量扫描 authors JSONB 字段，逐个提取 scholar_id 回查 Google Scholar 作者主页
- authors 字段是 JSONB 里的子元素而非独立列，需先 json.loads 再遍历
  （结构见本文件开头注释），拿到 author_url_googlescholar 后用 urlparse 解析出 user 参数
- 跨示例复用：sys.path 引入同级 google_scholar 示例的 GetAuthorByTitle / GetAuthorInfoById
- 动态批量线程池：先填满 max_workers，边完成边补充新任务（run_thread_dynamic_batch）
- 断点：log_offset 记录已扫描到的最大 id，重启从断点继续
- 双兜底：作者全部没有 GS 链接 / 全部查不到时，退化为按论文标题查作者

读取 article_paper_copilot 表 → 解析 authors JSONB → 批量回查 GS 作者 → 写入 scholar_author 表。
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 跨示例复用：引入同级 google_scholar 示例的类（运行时需两者都在 examples/ 下）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "google_scholar"))
from get_author_by_title import GetAuthorByTitle
from get_author_info_by_id import GetAuthorInfoById

from jimmyspider.cache import Cache
from jimmyspider.log_print import LogPrint
from jimmyspider.postgresql import PostgreSQLHandler


class GetAuthorPaperCopilot:
    def __init__(self):
        self.table_name_read = "article_paper_copilot"
        self.project_root = Path(__file__).resolve().parent.parent
        log_dir = self.project_root.joinpath("./logs")

        self.log_print = LogPrint(log_dir=log_dir, name=f"Get_Author_{self.table_name_read}")
        self.db_name = "postgres"
        self.log_offset = Cache(f"log_offset_get_author_{self.table_name_read}")
        self.postgreSQL_handler = PostgreSQLHandler(db_name=self.db_name, table_name=self.table_name_read,
                                                    return_type="dict")
        self.table_name_author = "scholar_author"
        self.create_table_author_info(table_name=self.table_name_author)
        min_id, max_id = self.postgreSQL_handler.getMinMaxId()
        self.min_id = min_id or 0
        self.max_id = max_id or 0
        self.log_print.print(f"table:{self.table_name_read} max_id: {self.max_id}, min_id: {self.min_id}")
        self.get_author_by_title = GetAuthorByTitle(table_name_read=self.table_name_read)
        self.get_author_info_by_id = GetAuthorInfoById()

    def create_table_author_info(self, table_name=None):
        """作者信息表（scholar_id 唯一键），DDL 用 handler.schema 补 schema 前缀。"""
        if not table_name:
            table_name = self.table_name_author
        sql = f'''
            CREATE TABLE IF NOT EXISTS "{self.postgreSQL_handler.schema}"."{table_name}" (
                id BIGSERIAL PRIMARY KEY,
                scholar_id VARCHAR(255) UNIQUE NOT NULL,
                name TEXT,
                avatar_url TEXT,
                profile_url TEXT,
                scholar_index JSONB,
                affiliation TEXT,
                category JSONB,
                cite_per_year JSONB,
                open_access_num INTEGER,
                non_open_access_num INTEGER,
                collaborator_list JSONB,
                article_list JSONB,
                create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        '''
        is_success = self.postgreSQL_handler.execute(sql)
        is_has_table = self.postgreSQL_handler.is_has_table(table_name)
        result = f"create table {table_name} is_success: {is_success}, is_has_table: {is_has_table}"
        self.log_print.print(result)

    def handle_one_article(self, authors, article_id: int, article_title: str = ""):
        """处理一篇文章的 authors JSONB：逐个 author 回查 GS 作者主页并入库。"""
        if isinstance(authors, str):
            try:
                authors = json.loads(authors)
            except Exception as e:
                self.log_print.error(f"json.loads(authors) error: {e}, authors: {authors}")
                return
        if not authors or not isinstance(authors, list):
            self.log_print.error(f"authors 为空 或者 不是 list , authors: {authors}")
            return
        author_info_list = []
        # 如果所有作者都没有 google 的 url（any 为 False），改走标题路线
        gs_url_list = [author.get("author_url_googlescholar", "").strip() for author in authors
                       if author.get("author_url_googlescholar", "").strip()]
        if not any(gs_url_list):
            self.log_print.error(f"authors 中所有作者都没有 author_url_googlescholar , 通过标题尝试 article_id: {article_id}")
            if article_title:
                self.get_author_by_title.handle_one_title(title=article_title, article_id=article_id)
                return
        for author in authors:
            author_name = author.get("author_name", "").strip()
            author_url_googlescholar = author.get("author_url_googlescholar", "").strip()
            if not author_name or not author_url_googlescholar:
                # 这种情况较多，不再逐条记录日志
                continue
            # 防数据异常：'https://scholar.google.com/citations?user=https://scholar.google.fr/citations?user=xxx'
            scholar_id = ""
            parsed_url = urlparse(author_url_googlescholar)
            if "user" in parse_qs(parsed_url.query):
                scholar_id = parse_qs(parsed_url.query)["user"][0]
            if not scholar_id:
                self.log_print.error(f"无法从 author_url_googlescholar 中提取 scholar_id , author_url_googlescholar: {author_url_googlescholar}")
                continue
            author_info, info = self.get_author_info_by_id.handle_one_scholar_id(scholar_id=scholar_id)
            if not author_info:
                self.log_print.error(
                    f"author_id: {scholar_id}, name: {author_name} get_author_info feedback: {info}, check please")
                continue
            if author_info is True:  # 已存在，跳过
                continue
            author_info_list.append(author_info)
        if author_info_list:
            try:
                author_rows = self.postgreSQL_handler.insert_data_list(
                    table_name=self.table_name_author,
                    data_list=author_info_list,
                    unique_col="scholar_id",
                )
                self.log_print.print(f"文章ID {article_id}  批量插入 {author_rows} 条作者数据成功。")
            except Exception as e:
                self.log_print.print(f"文章ID {article_id} 批量插入作者数据时出错: {e}")
        else:
            self.log_print.error(f"文章ID {article_id} 未获取到有效作者信息。")
            if article_title:
                self.get_author_by_title.handle_one_title(title=article_title, article_id=article_id)
                return

    def get_data_list_by_id(self, start_id, end_id):
        """按 id 范围读取论文，只取 2019 年之后的（authors 是 JSONB 子元素）。"""
        earliest_year = 2019
        sql = (f'SELECT id, article_title, authors FROM "{self.postgreSQL_handler.schema}"."{self.table_name_read}" '
               f"WHERE id >= {start_id} AND id <= {end_id} AND year::integer > {earliest_year} ORDER BY id ASC; ")
        data_list = self.postgreSQL_handler.execute_query(sql)
        return data_list

    def run_thread_dynamic_batch(self, max_workers=5, batch_size=40):
        """动态批量线程池：先填满 max_workers，任务完成时补充新任务，控制速率。"""
        current = self.log_offset.get_int(default=0)
        if not current or current < self.min_id:
            current = self.min_id
        start_continue_id = current
        self.log_print.print(f"开始运行，起始ID: {current}, 最大并发数: {max_workers}")
        first_continue = True
        while current <= self.max_id:
            data_list = self.get_data_list_by_id(current, current + batch_size - 1)
            current += batch_size
            self.log_offset.record_int(current)
            if not data_list:
                if first_continue:
                    first_continue = False
                    start_continue_id = current
                print(current, end=" -->")
                continue
            else:
                if not first_continue:
                    first_continue = True
                    self.log_print.print(f"在 ID 范围 [{start_continue_id}, {current}] 未找到符合要求数据")
                self.log_print.print(f"在ID范围 [{current}, {current + batch_size - 1}] 找到 {len(data_list)} 条数据，开始处理...")
            data_iter = iter(data_list)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_id = {}
                # 先填满线程池
                for _ in range(max_workers):
                    try:
                        data = next(data_iter)
                        future = executor.submit(
                            self.handle_one_article,
                            data["authors"],
                            data["id"],
                            data.get("article_title", ""),
                        )
                        future_to_id[future] = data["id"]
                    except StopIteration:
                        break
                    except Exception as e:
                        self.log_print.error(f"意料之外的报错,提交初始任务时发生错误: {e}")
                # 边完成边补充新任务
                data_exhausted = False
                while future_to_id:
                    done_futures = [f for f in future_to_id if f.done()]
                    for f in done_futures:
                        article_id = future_to_id.pop(f)
                        try:
                            f.result()
                            self.log_print.print(f"文章ID {article_id} 处理完成")
                        except Exception as e:
                            self.log_print.error(f"处理文章ID {article_id} 时发生错误: {e}")
                        # 补充新任务（如果还有数据）
                        if not data_exhausted:
                            try:
                                data = next(data_iter)
                                future = executor.submit(
                                    self.handle_one_article,
                                    data["authors"],
                                    data["id"],
                                    data.get("article_title", ""),
                                )
                                future_to_id[future] = data["id"]
                            except StopIteration:
                                data_exhausted = True  # 处理完了，下一批
                            except Exception as e:
                                self.log_print.error(f"意料之外的报错,提交新任务时发生错误: {e}")
                    # 没有任务完成且数据已耗尽时，减少等待时间
                    if not done_futures and data_exhausted:
                        time.sleep(0.1)
                    elif not done_futures:
                        time.sleep(1)
            self.log_print.print(f"已处理完 ID 范围 [{current - batch_size}, {current - 1}] 的所有任务")


if __name__ == "__main__":
    get_author_paper_copilot = GetAuthorPaperCopilot()
    get_author_paper_copilot.run_thread_dynamic_batch()

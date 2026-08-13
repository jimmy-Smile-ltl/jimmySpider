"""
Example: google_scholar — 流水线核心：按文章标题批量补作者信息。

从盛大网络 pro2_google_scholar 迁移。演示：
- 管道型脚本：扫描来源表（默认 article_arxiv_org，即 arxiv_org 示例的产出，
  也可用 sys.argv 指定其他表）的 id + article_title，逐条调用 get_article_by_title
- 搜索结果入库 article_search_by_google_scholar（article_url 唯一键 + origin 联合唯一约束），
  作者入库 scholar_author（scholar_id 唯一键）
- 作者 URL 解析：从搜索结果作者链接中提取 user 参数得到 scholar_id，
  再调 get_author_info_by_id 抓作者主页
- Redis 断点：log_offset 记录已处理到的来源表 id，中断可续
- 两个版本：handle_one_title 单条串行；handle_one_title_thread 内部多线程抓作者后批量入库
- run / run_thread 驱动整表扫描

按标题搜索 GS → 文章入库 → 提取 scholar_id 抓作者主页 → 作者入库。
"""

import concurrent.futures
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_article_by_title import GetArticleByTitle
from get_author_info_by_id import GetAuthorInfoById

from jimmyspider.cache import Cache
from jimmyspider.log_print import LogPrint
from jimmyspider.postgresql import PostgreSQLHandler
from jimmyspider.request import CurlRequestHandler


class GetAuthorByTitle:
    def __init__(self, table_name_read="article_arxiv_org"):
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
        self.log_print = LogPrint(log_dir=log_dir, name=f"GetAuthorByTitle_{table_name_read}")
        self.db_name = "postgres"
        self.table_name_read = table_name_read
        self.log_offset = Cache(f"log_offset_get_author_{table_name_read}")
        self.postgreSQL_handler = PostgreSQLHandler(db_name=self.db_name, table_name=self.table_name_read,
                                                    return_type="dict")
        self.table_name_article = "article_search_by_google_scholar"
        self.create_table_article_search(table_name=self.table_name_article)
        self.table_name_author = "scholar_author"
        self.create_table_author_info(table_name=self.table_name_author)
        min_id, max_id = self.postgreSQL_handler.getMinMaxId()
        self.min_id = min_id or 0
        self.max_id = max_id or 0
        self.log_print.print(f"table:{self.table_name_read} max_id: {self.max_id}, min_id: {self.min_id}")
        self.get_article = GetArticleByTitle()
        self.get_author = GetAuthorInfoById()

    def create_table_article_search(self, table_name=None):
        """搜索结果表：article_url 唯一 + origin 联合唯一约束（同文多次搜到只记一份）。"""
        if not table_name:
            table_name = self.table_name_article
        sql = f'''
            CREATE TABLE IF NOT EXISTS "{self.postgreSQL_handler.schema}"."{table_name}" (
                id BIGSERIAL PRIMARY KEY,
                article_title TEXT,
                article_url TEXT UNIQUE,
                author_dict_list JSONB,
                publish_info TEXT,
                origin_id BIGINT,
                cited_num INTEGER,
                html TEXT,
                origin_table VARCHAR(255),
                origin_title TEXT,
                article_idx INTEGER,
                create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT idx_articles_origin_article_unique UNIQUE (origin_id, origin_table, origin_title, article_idx)
            );
        '''
        is_success = self.postgreSQL_handler.execute(sql)
        is_has_table = self.postgreSQL_handler.is_has_table(table_name)
        result = f"create table {table_name} is_success: {is_success}, is_has_table: {is_has_table}"
        self.log_print.print(result)

    def create_table_author_info(self, table_name=None):
        """作者信息表（scholar_id 唯一键），与 get_author_from_paper_copilot 共用。"""
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

    def get_data_list_by_id(self, start_id, end_id):
        sql = f'SELECT id, article_title FROM "{self.postgreSQL_handler.schema}"."{self.table_name_read}" ' \
              f"WHERE id >= {start_id} AND id <= {end_id} ORDER BY id ASC;"
        data_list = self.postgreSQL_handler.execute_query(sql)
        return data_list

    def handle_one_title(self, title: str, article_id: int):
        """单条串行：搜索 → 文章入库 → 逐个作者抓主页入库。"""
        self.log_offset.record_int(article_id)
        start_time = time.time()
        if not title or not article_id:
            self.log_print.print(f"两者都不能为空 title: {title}, id: {article_id}")
            return
        article_list, info = self.get_article.handle_one_title(title=title)
        if not article_list:
            self.log_print.print(f"未找到结果: title={title}, article_id={article_id} info={info}")
            return
        self.log_print.print(f"article_id: {article_id}, title: {title}, article_len: {len(article_list)}")
        article_insert_list = []
        author_insert_list = []
        for idx, article in enumerate(article_list):
            article.update({
                "origin_id": article_id,
                "origin_table": self.table_name_read,
                "origin_title": title,
                "article_idx": idx + 1,
            })
            article_insert_list.append(article.copy())
            for author in article.get("author_dict_list", []):
                if not author.get("url"):
                    continue
                # 1. 解析 URL
                parsed_url = urlparse(author.get("url"))
                # 2. 将查询字符串解析为字典
                query_params = parse_qs(parsed_url.query)
                # 3. 从字典中获取 'user' 参数的值
                author_id = query_params.get("user", [None])[0]
                if not author_id:
                    print(f"url: {author.get('url')} not author_id ,check please")
                    continue
                author_info, info = self.get_author.handle_one_scholar_id(scholar_id=author_id)
                if not author_info:
                    self.log_print.print(
                        f"author_id: {author_id}, name: {author.get('name')} get_author_info {info}, check please")
                    continue
                if author_info is True:  # 已存在，跳过
                    continue
                author_insert_list.append(author_info.copy())
        if article_insert_list:
            insert_result = self.postgreSQL_handler.insert_data_list(
                table_name=self.table_name_article, data_list=article_insert_list, unique_col="article_url")
            self.log_print.print(f"insert_result    article: {insert_result}")
        if author_insert_list:
            insert_result = self.postgreSQL_handler.insert_data_list(
                table_name=self.table_name_author, data_list=author_insert_list, unique_col="scholar_id")
            self.log_print.print(f"insert_result    author: {insert_result}")
        end_time = time.time()
        self.log_print.print(
            f"标题与作者信息处理完毕 耗时{end_time - start_time:.4f}s  article_id: {article_id}, title: {title}")
        time.sleep(2)

    def handle_one_title_thread(self, title: str, article_id: int, max_workers=5):
        """优化版：内部多线程并发获取作者信息，全部完成后批量插入。"""
        self.log_offset.record_int(article_id)
        start_time = time.time()
        if not title or not article_id:
            self.log_print.print(f"标题或ID不能为空 title: {title}, id: {article_id}")
            return
        # 1. 获取文章列表
        article_list, info = self.get_article.handle_one_title(title=title)
        if not article_list:
            self.log_print.print(f"未找到文章结果: title={title}, article_id={article_id} info={info}")
            return
        self.log_print.print(f"article_id: {article_id}, title: {title}, 找到 {len(article_list)} 篇文章")
        articles_to_insert = []
        authors_to_fetch = {}  # 使用字典去重：{author_id: author_name}
        # 2. 收集需要插入的文章和需要获取的作者
        for idx, article in enumerate(article_list):
            article.update({
                "origin_id": article_id,
                "origin_table": self.table_name_read,
                "origin_title": title,
                "article_idx": idx + 1,
            })
            articles_to_insert.append(article)
            for author in article.get("author_dict_list", []):
                author_url = author.get("url")
                if not author_url:
                    continue
                query_params = parse_qs(urlparse(author_url).query)
                author_id = query_params.get("user", [None])[0]
                if author_id and author_id not in authors_to_fetch:
                    authors_to_fetch[author_id] = author.get("name")
        # 3. 并发获取作者信息
        authors_to_insert = []
        if authors_to_fetch:
            def _fetch_author_info(author_id, author_name):
                author_info, info = self.get_author.handle_one_scholar_id(scholar_id=author_id)
                if author_info and author_info is not True:
                    self.log_print.print(f"成功获取作者信息: id={author_id}, name={author_name}")
                    return author_info
                if author_info is True:
                    pass  # 已存在
                else:
                    self.log_print.print(f"获取作者信息失败: id={author_id}, name={author_name}, info={info}")
                return None

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_author = {
                    executor.submit(_fetch_author_info, author_id, name): author_id
                    for author_id, name in authors_to_fetch.items()
                }
                for future in concurrent.futures.as_completed(future_to_author):
                    try:
                        result = future.result()
                        if result:
                            authors_to_insert.append(result)
                    except Exception as exc:
                        author_id = future_to_author[future]
                        self.log_print.print(f"获取 author_id: {author_id} 的信息时产生异常: {exc}")
        # 4. 批量插入数据
        if articles_to_insert:
            try:
                article_rows = self.postgreSQL_handler.insert_data_list(
                    table_name=self.table_name_article, data_list=articles_to_insert, unique_col="article_url")
                self.log_print.print(f"批量插入 {article_rows} 条文章数据成功。")
            except Exception as e:
                self.log_print.print(f"批量插入文章数据时出错: {e}")
        if authors_to_insert:
            try:
                author_rows = self.postgreSQL_handler.insert_data_list(
                    table_name=self.table_name_author, data_list=authors_to_insert, unique_col="scholar_id")
                self.log_print.print(f"批量插入 {author_rows} 条作者数据成功。")
            except Exception as e:
                self.log_print.print(f"批量插入作者数据时出错: {e}")
        end_time = time.time()
        self.log_print.print(
            f"标题与作者信息处理完毕 耗时{end_time - start_time:.4f}s  article_id: {article_id}, title: {title}")
        time.sleep(2)  # 保留延时，避免对目标网站造成过大压力

    def run(self):
        current = self.log_offset.get_int(default=0)
        if not current or current < self.min_id:
            current = self.min_id
        while current <= self.max_id:
            self.log_offset.record_int(current)
            data_list = self.get_data_list_by_id(current, current + 20)
            if not data_list:
                self.log_print.print(f"未找到数据 id>={current} and id<={current + 20}  但是不会 break 继续下一个")
                current += 20
                continue
            for data in data_list:
                self.handle_one_title(title=data["article_title"], article_id=data["id"])
            current += 20
            time.sleep(5)
        self.log_print.print(f"处理完成，当前ID: {current}, 最大ID: {self.max_id}")

    def run_thread(self, max_workers=5):
        """线程池并发处理任务，批次提交 + 批次间延时控制请求速率。"""
        current = self.log_offset.get_int(default=0)
        if not current or current < self.min_id:
            current = self.min_id
        self.log_print.print(f"开始运行，起始ID: {current}, 最大并发数: {max_workers}")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while current <= self.max_id:
                self.log_offset.record_int(current)
                batch_size = 20
                data_list = self.get_data_list_by_id(current, current + batch_size - 1)
                if not data_list:
                    self.log_print.print(f"在 ID 范围 [{current}, {current + batch_size - 1}] 未找到数据，继续...")
                    current += batch_size
                    time.sleep(5)  # 即使没有数据，也稍微暂停一下，避免空轮询过快
                    continue
                for data in data_list:
                    executor.submit(self.handle_one_title_thread, title=data["article_title"], article_id=data["id"])
                self.log_print.print(
                    f"已提交 ID 范围 [{current}, {current + batch_size - 1}] 的 {len(data_list)} 个任务到线程池。")
                current += batch_size
                time.sleep(5)
        self.log_print.print(f"所有任务已提交处理完成，最终ID: {current}, 最大ID: {self.max_id}")


if __name__ == "__main__":
    # 默认处理 article_arxiv_org 表（arxiv_org 示例产出），可用 sys.argv 指定其他表
    table_name = sys.argv[1] if len(sys.argv) > 1 else "article_arxiv_org"
    get_author_by_title = GetAuthorByTitle(table_name_read=table_name)
    get_author_by_title.run_thread()

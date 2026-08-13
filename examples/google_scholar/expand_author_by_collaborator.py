"""
Example: google_scholar — 通过合作者向外扩散一层作者网络。

从盛大网络 pro2_google_scholar 迁移。演示：
- 种子作者筛选：读取 scholar_author 表的 category（领域）字段，
  只处理领域包含 machine learning / NLP 等关键词的作者（列表转小写，匹配大小写不敏感）
- 扩散：对每个种子作者的 collaborator_list JSONB 逐个回查作者主页
  （profile_url 里解析 user 参数拿 scholar_id，再调 get_author_info_by_id）
- 已存在作者（handle_one_scholar_id 返回 True）自动跳过，新作者批量 upsert 回 scholar_author 表
- Redis 断点：log_offset 记录已扫描到的种子作者表 id，中断可续

筛选领域匹配的作者 → 遍历其合作者 → 回查主页 → 新作者写回 scholar_author。
"""

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 跨文件复用：引入同目录示例类
sys.path.insert(0, str(Path(__file__).resolve().parent))
from get_author_info_by_id import GetAuthorInfoById

from jimmyspider.cache import Cache
from jimmyspider.log_print import LogPrint
from jimmyspider.postgresql import PostgreSQLHandler


class ExpandAuthorByCollaborator:
    def __init__(self):
        need_cat = ["Natural Language Processing", "Deep Learning", "Machine Learning", "Large Language Models",
                    "Large Language Model", "Reinforcement Learning", "Algorithms", "Artificial Intelligence",
                    "artificial general intelligence", "Federated Learning"]
        self.lower_need_cat = [cat.lower() for cat in need_cat]
        self.site = "https://scholar.google.com/"
        log_dir = Path(__file__).resolve().parent / "logs"
        log_name = "expand_author_by_collaborator"
        self.log_print = LogPrint(log_dir=log_dir, name=log_name)
        self.log_offset = Cache(f"log_offset_{log_name}")  # 记录上次处理到作者表的哪个 id
        self.db_name = "postgres"
        self.table_name = "scholar_author"  # 读取
        self.postgreSQL_handler = PostgreSQLHandler(db_name=self.db_name, table_name=self.table_name,
                                                    return_type="dict")
        min_id, max_id = self.postgreSQL_handler.getMinMaxId(table_name=self.table_name)
        self.log_print.print(f"postgreSQL {self.db_name}  表 {self.table_name}  max_id: {max_id} , min_id: {min_id}")
        self.min_id = min_id or 0
        self.max_id = max_id or 0
        self.get_author = GetAuthorInfoById()

    def get_one_batch(self, start_id, batch_size=100):
        """取一批有合作者的作者，只保留领域含目标关键词的种子。"""
        where_id_limit = f" where id >= {start_id} and id <= {start_id + batch_size} "
        where_value_limit = " and collaborator_list is not null "
        select_sql = (f"select id, scholar_id , category, collaborator_list "
                      f"from \"{self.postgreSQL_handler.schema}\".{self.table_name} {where_id_limit} {where_value_limit} "
                      f"order by id asc ;")
        data_list = self.postgreSQL_handler.execute_query(query=select_sql)
        # 过滤：领域包含特定领域（大小写不敏感）
        filter_list = []
        for data in data_list:
            research_area = data.get("category", {})
            if not research_area:
                continue
            match_flag = any(key.lower() in self.lower_need_cat for key in research_area.keys())
            if match_flag:
                filter_list.append(dict(data))
        return filter_list

    def check_collaborator_list(self, collaborator_list):
        """校验 collaborator_list 类型，字符串则 json.loads 转换。"""
        if not collaborator_list:
            self.log_print.print(f"collaborator_list 为 False, 跳过. value: {collaborator_list} check please")
            return False
        if isinstance(collaborator_list, list):
            self.log_print.print(f"collaborator_list 为list 类型正确，长度为 {len(collaborator_list)}")
            return True
        else:
            self.log_print.print(f"collaborator_list 不是 list 类型，尝试转换. value: {collaborator_list}")
            try:
                if isinstance(collaborator_list, str):
                    collaborator_list = json.loads(collaborator_list)
                else:
                    collaborator_list = list(collaborator_list)
                if not isinstance(collaborator_list, list):
                    self.log_print.print(
                        f"collaborator_list 转换后仍不是 list 类型，跳过. value: {collaborator_list} check please")
                    return False
                else:
                    self.log_print.print(f"collaborator_list 转换成功，长度为 {len(collaborator_list)}")
                    return True
            except Exception as e:
                self.log_print.print(
                    f"collaborator_list 转换失败，跳过. value: {collaborator_list}  error: {e} check please")
                return False

    def process_batch(self, filter_list):
        """处理一批种子作者：遍历其合作者，回查主页，新作者批量入库。"""
        all_count = 0
        for data in filter_list:
            author_list = []
            collaborator_list = data.get("collaborator_list", [])
            if not self.check_collaborator_list(collaborator_list):
                continue
            for collaborator in collaborator_list:
                profile_url = collaborator.get("profile_url")
                parsed_url = urlparse(profile_url)
                if "user" in parse_qs(parsed_url.query):
                    scholar_id = parse_qs(parsed_url.query)["user"][0]
                else:
                    scholar_id = profile_url
                author_info, info = self.get_author.handle_one_scholar_id(scholar_id=scholar_id, enforce=False)
                if not author_info:
                    self.log_print.print(f"scholar_id: {scholar_id}, name:  get_author_info {info}, check please")
                    continue
                if author_info is True:  # 已存在
                    continue
                author_list.append(author_info)
                self.log_print.print(f"scholar_id: {scholar_id}, name: {author_info.get('name')} get_author_info success")
            self.log_offset.record_int(data.get("id"))
            if author_list:
                all_count += len(author_list)
                info = self.postgreSQL_handler.insert_data_list(
                    table_name=self.table_name, data_list=author_list, unique_col="scholar_id")
                self.log_print.print(
                    f"ID  {data.get('id')} ,  scholar_id: {data.get('scholar_id')}  add {len(author_list)} data, info: {info}")
        return all_count

    def run(self):
        batch_size = 500
        start_id = self.log_offset.get_int(default=self.min_id)
        self.log_print.print(f"start_id: {start_id}")
        while True:
            self.log_offset.record_int(start_id)
            if start_id > self.max_id:
                self.log_print.print(f"all done, max_id: {self.max_id}, now process start_id {start_id} exit")
                break
            filter_list = self.get_one_batch(start_id=start_id, batch_size=batch_size)
            start_id += batch_size
            if not filter_list:
                self.log_print.print(f"ID from {start_id} to {start_id + batch_size} no error data, continue next batch")
                continue
            all_count = self.process_batch(filter_list)
            self.log_print.print(f"ID from {start_id} to {start_id + batch_size}  add {all_count} data,")


if __name__ == "__main__":
    expand_author = ExpandAuthorByCollaborator()
    expand_author.run()

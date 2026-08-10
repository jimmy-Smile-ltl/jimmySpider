"""
jimmySpider - 爬虫基类

提供爬虫项目的统一入口，自动装配数据库、缓存、文件下载、代理、日志等组件。
支持 MongoDB（默认）/ MySQL / PostgreSQL 三种数据库后端。
"""

import time
from pathlib import Path

from jimmyspider.cache import Cache
from jimmyspider.config import get_config
from jimmyspider.html import handleHTML
from jimmyspider.file import FileDownloader
from jimmyspider.request import (
    SingleRequestHandler,
    AsyncRequestHandler,
    ThreadRequestHandler,
)
from jimmyspider.log_print import LogPrint
from jimmyspider.soup import extractSoup

# 默认数据库：MongoDB（必装）
from jimmyspider.mongo import HandleMongoDB

# MySQL / PostgreSQL 为可选依赖
try:
    from jimmyspider.mysql import MySQLHandler
except ImportError:
    MySQLHandler = None

try:
    from jimmyspider.postgresql import PostgreSQLHandler
except ImportError:
    PostgreSQLHandler = None


class JimmySpider:
    """爬虫基类，初始化时自动装配所有组件。

    使用方式:
        # 默认 MongoDB
        class MySpider(JimmySpider):
            def run(self):
                res = self.single_fetcher.fetch(url)
                self.save_result({"_id": "xxx", "data": res[url]})

        if __name__ == "__main__":
            MySpider(pro_path=Path(__file__).parent).run()

    切换数据库:
        # 方式一：配置文件 jimmyspider.yaml 中设置 db_type
        # db_type: "mysql"  或  db_type: "postgresql"

        # 方式二：构造函数传参
        MySpider(pro_path=..., db_type="mysql", db_name="my_db")
    """

    def __init__(self, **kwargs):
        pro_path = kwargs.get("pro_path", "")
        pro_name = Path(pro_path).name

        if pro_name:
            print(f"正在初始化爬虫，项目名称：{pro_name} ")
        else:
            raise ValueError("必须有 pro_path 参数")

        self.test_url = kwargs.get("test_url", None)
        self.project_root = Path(pro_path)

        if "table_name" not in kwargs:
            self.table_name = pro_name
        else:
            self.table_name = kwargs["table_name"]

        log_dir = self.project_root / "logs"
        self.log_print = LogPrint(log_dir=log_dir, name=self.table_name)

        # ---- 数据库：按 db_type 选择后端 ----
        config = get_config()
        db_type = kwargs.get("db_type", config.DB_TYPE)
        db_name = kwargs.get("db_name", None)

        if db_type == "mysql":
            if MySQLHandler is None:
                raise ImportError("MySQL 需要 pymysql，请执行 pip install pymysql")
            self.db_manager = MySQLHandler(
                db_name=db_name or config.MYSQL_DB,
                table_name=self.table_name,
            )
        elif db_type == "postgresql":
            if PostgreSQLHandler is None:
                raise ImportError(
                    "PostgreSQL 需要 psycopg2-binary，请执行 pip install psycopg2-binary"
                )
            self.db_manager = PostgreSQLHandler(
                table_name=self.table_name,
                db_name=db_name or config.PG_DB,
                schema=kwargs.get("pg_schema", config.PG_SCHEMA),
            )
        else:
            # 默认 MongoDB
            self.db_manager = HandleMongoDB(table_name=self.table_name)

        self.db_type = db_type

        self.html_saver = handleHTML(pro_name=self.table_name)
        self.file_saver = FileDownloader(pro_name=self.table_name)

        self.start_time = time.time()
        self.insert_num = 0

        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        self.async_fetcher = AsyncRequestHandler(test_url=self.test_url)
        self.thread_fetcher = ThreadRequestHandler(test_url=self.test_url)
        self.extract_soup = extractSoup()

    @staticmethod
    def format_duration(seconds: float) -> str:
        """将秒数格式化为 'X天Y小时Z分钟W秒'"""
        total_secs = int(seconds)
        days = total_secs // 86400
        hours = (total_secs % 86400) // 3600
        minutes = (total_secs % 3600) // 60
        secs = total_secs % 60

        if days == 0 and hours == 0 and minutes == 0 and secs == 0:
            return "0秒"

        parts = []
        if days:
            parts.append(f"{days}天")
        if hours:
            parts.append(f"{hours}小时")
        if minutes:
            parts.append(f"{minutes}分钟")
        if secs or not parts:
            parts.append(f"{secs}秒")
        return "".join(parts)

    def save_result(self, insert_list):
        """保存结果到数据库。自动适配 MongoDB / MySQL / PostgreSQL。

        MongoDB 模式：每条记录需有 _id 字段（URL 的 MD5）。
        MySQL/PG 模式：自动使用 _unique_key 或 _id 或 url 字段做唯一键。
        """
        if self.db_type == "mongodb":
            if isinstance(insert_list, list):
                self.db_manager.insert_many(insert_list)
                self.insert_num += len(insert_list)
            elif isinstance(insert_list, dict):
                self.db_manager.insert_one(insert_list)
                self.insert_num += 1
            else:
                raise TypeError(
                    f"save_result: insert_list 类型不支持 =={type(insert_list)}=="
                )
        else:
            # MySQL / PostgreSQL：统一使用 insert_data / insert_data_list
            if isinstance(insert_list, list):
                self.db_manager.insert_data_list(insert_list)
                self.insert_num += len(insert_list)
            elif isinstance(insert_list, dict):
                self.db_manager.insert_data(insert_list)
                self.insert_num += 1
            else:
                raise TypeError(
                    f"save_result: insert_list 类型不支持 =={type(insert_list)}=="
                )

        cost_time = time.time() - self.start_time
        self.log_print.print(
            f"插入效率计算 {self.insert_num / (int(cost_time) + 1):.4f} 行/秒, "
            f"总运行时间：{self.format_duration(cost_time)}  "
            f"插入总行数 {self.insert_num}  [{self.db_type}]"
        )

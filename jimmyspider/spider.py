"""
jimmySpider - 爬虫基类

提供爬虫项目的统一入口，自动装配数据库、缓存、文件下载、代理、日志等组件。
"""

import time
from pathlib import Path

from jimmyspider.cache import Cache
from jimmyspider.html import handleHTML
from jimmyspider.mongo import HandleMongoDB
from jimmyspider.file import FileDownloader
from jimmyspider.request import (
    SingleRequestHandler,
    AsyncRequestHandler,
    ThreadRequestHandler,
)
from jimmyspider.log_print import LogPrint
from jimmyspider.soup import extractSoup


class JimmySpider:
    """爬虫基类，初始化时自动装配所有组件。

    使用方式:
        class MySpider(JimmySpider):
            def run(self):
                # self.single_fetcher.fetch(url)
                # self.save_result(data)
                pass

        if __name__ == "__main__":
            MySpider(pro_path=Path(__file__).parent).run()
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

        self.db_manager = HandleMongoDB(table_name=self.table_name)
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
        """将秒数格式化为 'X天Y小时Z分钟W秒' 的易读字符串"""
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
        """保存结果到 MongoDB"""
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

        cost_time = time.time() - self.start_time
        self.log_print.print(
            f"插入效率计算 {self.insert_num / (int(cost_time) + 1):.4f} 行/秒, "
            f"总运行时间：{self.format_duration(cost_time)}  插入总行数 {self.insert_num}"
        )

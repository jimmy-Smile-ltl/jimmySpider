"""
hello_mysql — MySQL 数据库示例

与 hello_world 完全相同的爬虫逻辑，唯一区别：
通过 db_type="mysql" 切换数据库后端为 MySQL。

前置条件：
    pip install pymysql
    创建 MySQL 数据库 jimmyspider（或修改 jimmyspider.yaml 中的 mysql_db）
    表结构由 MySQLHandler 自动创建
"""
from pathlib import Path
from jimmyspider import JimmySpider, Cache, generate_string_id


class Spider(JimmySpider):
    def __init__(self, **kwargs):
        # 关键：通过 db_type 切换数据库
        kwargs.setdefault("db_type", "mysql")
        kwargs.setdefault("test_url", "https://news.ycombinator.com/")
        super().__init__(**kwargs)
        self.page = Cache(f"{self.table_name}_page")
        self.finished = Cache(f"{self.table_name}_finished")

    def run(self):
        if self.finished.get_string():
            self.log_print.info("已完成，跳过")
            return

        page = self.page.get_int(default=1)
        while page <= 2:  # 只爬 2 页做演示
            url = f"https://news.ycombinator.com/?p={page}"
            self.log_print.info(f"抓取第 {page} 页")

            res = self.single_fetcher.fetch(url)
            if not res:
                break

            html = res[url]
            titles = self.extract_soup.extract_texts(html, "span.titleline > a")
            urls = self.extract_soup.extract_list_url(html, "span.titleline > a")

            items = []
            for title, link in zip(titles, urls):
                items.append({
                    "_unique_key": generate_string_id(link),
                    "url": link,
                    "title": title,
                })

            if not items:
                break

            self.save_result(items)  # 自动调用 MySQLHandler.insert_data_list
            self.page.record_int(page)
            page += 1

        self.finished.record_string("done")
        self.log_print.info("MySQL 示例完成")


if __name__ == "__main__":
    Spider(pro_path=Path(__file__).parent).run()

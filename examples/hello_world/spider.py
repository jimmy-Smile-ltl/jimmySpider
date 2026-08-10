"""
hello_world —— jimmySpider 框架入门示例

抓取 Hacker News 首页的新闻标题，演示 jimmySpider 的最小完整用法：
1. 继承 JimmySpider 基类 —— 自动装配日志 / MongoDB / HTML 解析等组件
2. 使用 SingleRequestHandler 发送 GET 请求
3. 使用 extractSoup 解析 HTML
4. 调用 save_result() 将结果写入 MongoDB
"""

from pathlib import Path

from bs4 import BeautifulSoup

from jimmyspider.request import SingleRequestHandler
from jimmyspider.spider import JimmySpider
from jimmyspider.tool import generate_string_id


class Spider(JimmySpider):
    """最简单的爬虫：抓取 Hacker News 首页新闻标题"""

    def __init__(self, *args, **kwargs):
        # 基类会根据 pro_path 自动装配：log_print 日志、db_manager 数据库、
        # extract_soup HTML 解析、html_saver HTML 保存、三个请求处理器等。
        super().__init__(*args, **kwargs)
        # 请求处理器：SingleRequestHandler 是最常用的同步处理器。
        # test_url 用来探测可用代理；未配置代理时直连。
        self.single_fetcher = SingleRequestHandler(test_url=self.test_url)
        # 抓取目标：Hacker News 首页（纯 HTML，无需登录、无反爬）
        self.list_url = "https://news.ycombinator.com/"

    def run(self):
        # 第 1 步：发送 GET 请求，成功返回 requests.Response，失败返回 None
        response = self.single_fetcher.fetch(self.list_url)
        if not response:
            self.log_print.error("请求失败，请检查网络后重试")
            return

        # 第 2 步：把响应文本解析成 BeautifulSoup 对象
        soup = BeautifulSoup(response.text, "html.parser")

        # 第 3 步：用 extractSoup 批量提取标题文本与链接地址
        #   span.titleline > a 是首页每条新闻的标题元素
        titles = self.extract_soup.extract_texts(soup, "span.titleline > a")
        links = self.extract_soup.extract_list_url(soup, "span.titleline > a")

        # 第 4 步：组装入库数据，_id 是 MongoDB 去重/更新的唯一键
        data_list = [
            {"_id": generate_string_id(link), "标题": title, "链接": link}
            for title, link in zip(titles, links)
        ]

        # 第 5 步：批量保存到 MongoDB（collection 名 = 项目目录名 hello_world）
        self.save_result(insert_list=data_list)
        self.log_print.print(f"采集完成，共 {len(data_list)} 条新闻")


if __name__ == "__main__":
    # pro_path 指向项目目录，基类据此确定 collection 名与日志目录
    spider = Spider(pro_path=Path(__file__).parent)
    spider.run()

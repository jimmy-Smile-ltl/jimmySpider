"""
中间件基类

DownloaderMiddleware: 处理请求/响应的中间件链
SpiderMiddleware:    处理爬虫输入/输出的中间件链
"""

import logging
from typing import Optional

from .request import Request, Response

logger = logging.getLogger(__name__)


class DownloaderMiddleware:
    """
    下载器中间件基类

    处理顺序: process_request → 下载 → process_response
    """

    def process_request(self, request: Request) -> Optional[Request]:
        """
        处理请求（下载前）

        Returns:
            Request — 继续传递修改后的请求
            Response — 短路，直接返回此响应
            None     — 继续传递原请求
        """
        return None

    def process_response(self, request: Request, response: Response) -> Optional[Response]:
        """
        处理响应（下载后）

        Returns:
            Response — 继续传递
            Request  — 重新下载
        """
        return response

    def process_exception(self, request: Request, exception: Exception):
        """
        处理下载异常

        Returns:
            Response — 用此响应恢复
            Request  — 重试下载
            None     — 异常继续向上传播
        """
        return None


class SpiderMiddleware:
    """
    爬虫中间件基类

    处理顺序: process_spider_input → spider.parse() → process_spider_output
    """

    def process_spider_input(self, response: Response) -> Optional[Response]:
        """
        处理进入爬虫的响应

        Returns:
            Response — 继续传递
            None     — 丢弃此响应
        """
        return response

    def process_spider_output(self, response: Response, result: list) -> list:
        """
        处理爬虫产出

        Returns:
            list — 处理后的结果列表
        """
        return result

    def process_spider_exception(self, response: Response, exception: Exception) -> list:
        """
        处理爬虫异常

        Returns:
            list — 替代产出
        """
        return []

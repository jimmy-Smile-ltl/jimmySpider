"""
Scrapy 风格 Spider Middleware 管理器

类比 Scrapy 的 SpiderMiddlewareManager
"""

import logging
from typing import Generator

from ..common.request import Request, Response
from ..common.middleware import SpiderMiddleware

logger = logging.getLogger(__name__)


class SpiderMiddlewareManager:
    """
    爬虫中间件管理器

    流程:
      response → process_spider_input → spider.parse() → process_spider_output → items/requests
    """

    def __init__(self, middlewares: list[SpiderMiddleware] = None):
        self.middlewares: list[SpiderMiddleware] = middlewares or []

    def scrape_response(self, scrape_func, response: Response) -> list:
        """
        执行完整的爬虫中间件链

        Args:
            scrape_func: 实际调用 spider.parse/spider.callback 的函数
            response: 待处理的响应

        Returns:
            list[Request | dict]: 爬虫产出
        """
        # Step 1: process_spider_input 链
        for mw in self.middlewares:
            try:
                result = mw.process_spider_input(response)
                if result is None:
                    logger.debug(f"{mw.__class__.__name__}.process_spider_input 丢弃响应")
                    return []
                response = result
            except Exception as e:
                logger.error(f"{mw.__class__.__name__}.process_spider_input 异常: {e}")
                return []

        # Step 2: 执行 Spider 回调
        try:
            output = scrape_func(response)
            if not isinstance(output, (list, Generator)):
                output = list(output) if hasattr(output, "__iter__") else [output]
        except Exception as e:
            logger.error(f"Spider 回调异常: {e}")
            for mw in self.middlewares:
                try:
                    result = mw.process_spider_exception(response, e)
                    if result:
                        output = result
                        break
                except Exception as ex:
                    logger.error(f"{mw.__class__.__name__}.process_spider_exception 异常: {ex}")
            else:
                return []

        # Step 3: process_spider_output 链
        result_list = list(output) if not isinstance(output, list) else output
        for mw in self.middlewares:
            try:
                result_list = mw.process_spider_output(response, result_list)
            except Exception as e:
                logger.error(f"{mw.__class__.__name__}.process_spider_output 异常: {e}")

        return result_list

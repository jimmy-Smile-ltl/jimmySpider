"""
asyncio Spider Middleware 管理器

与 Scrapy 版本接口一致，支持协程中间件
"""

import asyncio
import logging
from typing import Generator

from ..common.request import Request, Response
from ..common.middleware import SpiderMiddleware

logger = logging.getLogger(__name__)


class AioSpiderMiddlewareManager:
    """
    异步爬虫中间件管理器

    流程:
      response → process_spider_input → spider.parse() → process_spider_output → items/requests
    """

    def __init__(self, middlewares: list[SpiderMiddleware] = None):
        self.middlewares: list[SpiderMiddleware] = middlewares or []

    async def scrape_response(self, scrape_func, response: Response) -> list:
        """
        异步执行爬虫中间件链
        """
        # Step 1: process_spider_input 链
        for mw in self.middlewares:
            try:
                result = mw.process_spider_input(response)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is None:
                    return []
                response = result
            except Exception as e:
                logger.error(f"{mw.__class__.__name__}.process_spider_input 异常: {e}")
                return []

        # Step 2: 执行 Spider 回调
        try:
            output = scrape_func(response)
            if asyncio.iscoroutine(output):
                output = await output
            if not isinstance(output, list):
                output = list(output) if hasattr(output, "__iter__") else [output]
        except Exception as e:
            logger.error(f"Spider 回调异常: {e}")
            for mw in self.middlewares:
                try:
                    result = mw.process_spider_exception(response, e)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if result:
                        output = result
                        break
                except Exception as ex:
                    logger.error(f"中间件异常处理失败: {ex}")
            else:
                return []

        # Step 3: process_spider_output 链
        result_list = list(output) if not isinstance(output, list) else output
        for mw in self.middlewares:
            try:
                result = mw.process_spider_output(response, result_list)
                if asyncio.iscoroutine(result):
                    result = await result
                result_list = result
            except Exception as e:
                logger.error(f"{mw.__class__.__name__}.process_spider_output 异常: {e}")

        return result_list

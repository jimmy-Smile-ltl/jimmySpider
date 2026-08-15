"""
爬虫基类

定义爬虫的通用接口：name, start_requests, parse
"""

import time
import logging
from typing import Generator, Optional

from .request import Request, Response

logger = logging.getLogger(__name__)


class BaseSpider:
    """爬虫基类 — 两种调度器通用"""

    name: str = "base_spider"

    # 统计
    stats: dict = None

    def __init__(self, name: str = None, **kwargs):
        if name:
            self.name = name
        self.stats = {
            "start_time": time.time(),
            "request_count": 0,
            "response_count": 0,
            "item_count": 0,
            "error_count": 0,
        }

    def start_requests(self) -> list[Request]:
        """生成起始请求，子类覆盖"""
        return []

    def parse(self, response: Response) -> list:
        """
        解析响应，子类覆盖

        yield Request → 新请求入队
        yield dict     → Item (输出)
        """
        return []

    def process_item(self, item: dict) -> dict:
        """处理提取到的 Item（默认打印统计）"""
        self.stats["item_count"] += 1
        return item

    def __repr__(self):
        return f"<Spider {self.name}>"

    def summary(self) -> str:
        elapsed = time.time() - self.stats["start_time"]
        return (f"Spider[{self.name}]: {self.stats['request_count']} reqs, "
                f"{self.stats['response_count']} resps, "
                f"{self.stats['item_count']} items, "
                f"{self.stats['error_count']} errors, "
                f"{elapsed:.1f}s")

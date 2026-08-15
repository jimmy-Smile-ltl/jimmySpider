"""
Scrapy 风格信号系统

基于发布-订阅模式，支持同步回调链。
模拟 Scrapy 的信号机制，使用简单的回调列表代替 Twisted 的 SignalManager。
"""

import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger(__name__)


class SignalManager:
    """
    信号管理器

    支持的信号:
        engine_started, engine_stopped
        spider_opened, spider_closed
        request_scheduled, request_dropped
        response_received, response_downloaded
        item_scraped
    """

    # 标准信号列表
    SIGNALS = [
        "engine_started", "engine_stopped",
        "spider_opened", "spider_closed",
        "request_scheduled", "request_dropped",
        "response_received", "response_downloaded",
        "request_received",  # 下载器收到请求
        "item_scraped",
    ]

    def __init__(self):
        self._receivers: dict[str, list[Callable]] = defaultdict(list)

    def connect(self, signal: str, receiver: Callable) -> None:
        """注册信号接收器"""
        if signal not in self.SIGNALS:
            logger.warning(f"未知信号: {signal}")
        self._receivers[signal].append(receiver)

    def disconnect(self, signal: str, receiver: Callable) -> None:
        """移除信号接收器"""
        if receiver in self._receivers.get(signal, []):
            self._receivers[signal].remove(receiver)

    def send(self, signal: str, **kwargs) -> list[Any]:
        """
        发送信号，调用所有注册的接收器

        Returns:
            所有接收器的返回值列表
        """
        results = []
        for receiver in self._receivers.get(signal, []):
            try:
                result = receiver(**kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"信号接收器异常 signal={signal} receiver={receiver}: {e}")
        return results

    def send_catch_log(self, signal: str, **kwargs) -> list[Any]:
        """发送信号并捕获异常日志"""
        return self.send(signal, **kwargs)

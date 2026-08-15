"""
asyncio 信号系统

使用 asyncio.Event + 回调列表实现异步信号机制
"""

import asyncio
import logging
from collections import defaultdict
from typing import Callable, Any, Coroutine

logger = logging.getLogger(__name__)


class AioSignalManager:
    """
    异步信号管理器

    支持同步回调和异步协程回调
    """

    SIGNALS = [
        "engine_started", "engine_stopped",
        "spider_opened", "spider_closed",
        "request_scheduled", "request_dropped",
        "response_received", "response_downloaded",
        "item_scraped",
    ]

    def __init__(self):
        self._receivers: dict[str, list[Callable]] = defaultdict(list)

    def connect(self, signal: str, receiver: Callable) -> None:
        self._receivers[signal].append(receiver)

    def disconnect(self, signal: str, receiver: Callable) -> None:
        if receiver in self._receivers.get(signal, []):
            self._receivers[signal].remove(receiver)

    async def send(self, signal: str, **kwargs) -> list[Any]:
        """异步发送信号（支持协程回调）"""
        results = []
        for receiver in self._receivers.get(signal, []):
            try:
                result = receiver(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                results.append(result)
            except Exception as e:
                logger.error(f"信号接收器异常 signal={signal}: {e}")
        return results

    def send_sync(self, signal: str, **kwargs) -> list[Any]:
        """同步发送信号（仅同步回调）"""
        results = []
        for receiver in self._receivers.get(signal, []):
            try:
                result = receiver(**kwargs)
                if not asyncio.iscoroutine(result):
                    results.append(result)
            except Exception as e:
                logger.error(f"信号接收器异常 signal={signal}: {e}")
        return results

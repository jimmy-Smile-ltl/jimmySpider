"""
分布式代理 — 抽象接口

所有代理后端必须实现此接口。
参考 jimmyspider/proxy.py 的 ProxyManager 和 ProxyUtil 设计。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import time


@dataclass
class ProxyInfo:
    """代理信息"""
    host: str
    port: int
    scheme: str = "http"           # http / https / socks5
    username: str = ""
    password: str = ""
    source: str = "unknown"        # redis_pool / clash / tunnel_api / static
    latency_ms: float = 0.0
    success_count: int = 0
    fail_count: int = 0
    last_used: float = 0.0
    last_checked: float = 0.0
    is_alive: bool = True
    tags: list = field(default_factory=list)  # e.g. ["domestic", "foreign"]

    @property
    def proxy_url(self) -> str:
        """生成 requests 格式的代理 URL"""
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    @property
    def proxy_dict(self) -> dict:
        """生成 requests 格式的 proxies 字典"""
        url = self.proxy_url
        return {"http": url, "https": url}

    def record_success(self):
        self.success_count += 1
        self.last_used = time.time()

    def record_failure(self):
        self.fail_count += 1
        self.last_used = time.time()

    @property
    def score(self) -> float:
        """质量评分: 成功率 + 延迟惩罚"""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.5
        success_rate = self.success_count / total
        latency_penalty = min(self.latency_ms / 5000, 1.0) * 0.3
        return max(success_rate - latency_penalty, 0.0)


class ProxyBackend(ABC):
    """
    代理后端抽象基类

    每个后端实现负责:
      - 获取代理
      - 报告代理状态（成功/失败）
      - 健康检查
      - 提供统计信息
    """

    backend_name: str = "base"

    @abstractmethod
    async def get_proxy(self, tags: list[str] = None) -> Optional[ProxyInfo]:
        """获取一个可用代理"""
        ...

    @abstractmethod
    async def report_success(self, proxy: ProxyInfo) -> None:
        """报告代理使用成功"""
        ...

    @abstractmethod
    async def report_failure(self, proxy: ProxyInfo, error: str = "") -> None:
        """报告代理使用失败"""
        ...

    @abstractmethod
    async def health_check(self) -> dict:
        """健康检查 → 返回后端状态"""
        ...

    @abstractmethod
    async def get_stats(self) -> dict:
        """获取统计信息"""
        ...

    async def close(self) -> None:
        """关闭后端连接"""
        pass

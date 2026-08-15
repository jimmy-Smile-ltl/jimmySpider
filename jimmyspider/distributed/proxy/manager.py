"""
分布式代理管理器

统一管理多个代理后端，支持:
  - 多后端负载均衡（优先级 + 权重）
  - 后端故障自动降级
  - 全局代理质量追踪
  - 与消息队列集成（为每个 Worker 分配代理）
"""

import asyncio
import time
import random
from typing import Optional

from .base import ProxyBackend, ProxyInfo


class DistributedProxyManager:
    """
    分布式代理管理器

    管理多个代理后端，按优先级和权重分配代理。

    策略:
      - primary: 优先使用高优先级后端
      - fallback: 主后端失败时切换到备用后端
      - round_robin: 轮询所有后端
      - weighted: 按权重随机

    使用示例:
        manager = DistributedProxyManager()
        manager.add_backend(redis_pool, priority=1, weight=10)
        manager.add_backend(clash_pool, priority=2, weight=5)
        proxy = await manager.get_proxy(tags=["domestic"])
    """

    def __init__(self, strategy: str = "primary"):
        """
        Args:
            strategy: primary / fallback / round_robin / weighted
        """
        self.strategy = strategy
        self._backends: list[dict] = []  # [{backend, priority, weight, healthy}]
        self._rr_index = 0
        self._stats = {
            "total_requests": 0,
            "total_successes": 0,
            "total_failures": 0,
        }

    def add_backend(self, backend: ProxyBackend, priority: int = 1,
                    weight: int = 10) -> "DistributedProxyManager":
        """添加代理后端"""
        self._backends.append({
            "backend": backend,
            "priority": priority,
            "weight": weight,
            "healthy": True,
            "last_error": None,
            "error_count": 0,
        })
        return self

    def remove_backend(self, backend_name: str) -> bool:
        """移除代理后端"""
        for i, b in enumerate(self._backends):
            if b["backend"].backend_name == backend_name:
                self._backends.pop(i)
                return True
        return False

    # ---- 获取代理 ----
    async def get_proxy(self, tags: list[str] = None) -> Optional[ProxyInfo]:
        """
        获取代理（带后端故障自动降级）
        """
        self._stats["total_requests"] += 1

        candidates = self._get_candidates()
        if not candidates:
            return None

        # 按策略选择后端
        if self.strategy == "primary":
            proxy = await self._try_backends(candidates, tags)
        elif self.strategy == "fallback":
            proxy = await self._try_fallback(candidates, tags)
        elif self.strategy == "weighted":
            proxy = await self._try_weighted(candidates, tags)
        else:  # round_robin
            proxy = await self._try_round_robin(candidates, tags)

        return proxy

    def _get_candidates(self) -> list[dict]:
        """获取候选后端列表"""
        healthy = [b for b in self._backends if b["healthy"]]
        if not healthy:
            # 全部不健康 → 重置
            for b in self._backends:
                b["healthy"] = True
                b["error_count"] = 0
            healthy = self._backends
        # 按优先级排序（priority 小的优先）
        return sorted(healthy, key=lambda b: b["priority"])

    async def _try_backends(self, candidates: list[dict],
                            tags: list[str] = None) -> Optional[ProxyInfo]:
        """按顺序尝试每个后端"""
        for b in candidates:
            proxy = await b["backend"].get_proxy(tags)
            if proxy:
                return proxy
            b["error_count"] += 1
            if b["error_count"] >= 3:
                b["healthy"] = False
        return None

    async def _try_fallback(self, candidates: list[dict],
                            tags: list[str] = None) -> Optional[ProxyInfo]:
        """Fallback 策略: 主后端失败后用备后端"""
        primary = candidates[:1] if candidates else []
        fallbacks = candidates[1:] if len(candidates) > 1 else []
        for b in primary + fallbacks:
            proxy = await b["backend"].get_proxy(tags)
            if proxy:
                return proxy
        return None

    async def _try_weighted(self, candidates: list[dict],
                            tags: list[str] = None) -> Optional[ProxyInfo]:
        """加权随机"""
        total_weight = sum(b["weight"] for b in candidates)
        for b in candidates:
            b["_prob"] = b["weight"] / total_weight
        # 按概率随机选，最多尝试 3 次
        for _ in range(min(len(candidates), 3)):
            r = random.random()
            cumulative = 0
            for b in candidates:
                cumulative += b["_prob"]
                if r <= cumulative:
                    proxy = await b["backend"].get_proxy(tags)
                    if proxy:
                        return proxy
                    break
        return None

    async def _try_round_robin(self, candidates: list[dict],
                               tags: list[str] = None) -> Optional[ProxyInfo]:
        """轮询"""
        for _ in range(len(candidates)):
            self._rr_index = (self._rr_index + 1) % len(candidates)
            b = candidates[self._rr_index]
            proxy = await b["backend"].get_proxy(tags)
            if proxy:
                return proxy
        return None

    # ---- 报告 ----
    async def report_success(self, proxy: ProxyInfo) -> None:
        self._stats["total_successes"] += 1
        for b in self._backends:
            if b["backend"].backend_name == proxy.source:
                await b["backend"].report_success(proxy)
                b["error_count"] = max(0, b["error_count"] - 1)
                break

    async def report_failure(self, proxy: ProxyInfo, error: str = "") -> None:
        self._stats["total_failures"] += 1
        for b in self._backends:
            if b["backend"].backend_name == proxy.source:
                await b["backend"].report_failure(proxy, error)
                b["error_count"] += 1
                b["last_error"] = error
                if b["error_count"] >= 5:
                    b["healthy"] = False
                break

    # ---- 健康检查 ----
    async def health_check(self) -> dict:
        """检查所有后端健康状态"""
        results = {}
        for b in self._backends:
            try:
                hc = await b["backend"].health_check()
                results[b["backend"].backend_name] = {
                    **hc,
                    "manager_healthy": b["healthy"],
                    "error_count": b["error_count"],
                }
            except Exception as e:
                results[b["backend"].backend_name] = {
                    "error": str(e), "manager_healthy": False
                }
                b["healthy"] = False
        return results

    async def get_stats(self) -> dict:
        return {
            **self._stats,
            "backends": await self.health_check(),
            "success_rate": (self._stats["total_successes"] /
                           max(self._stats["total_requests"], 1)),
        }

    async def close(self) -> None:
        for b in self._backends:
            await b["backend"].close()

"""
分布式健康检查系统

对集群中所有组件执行定期健康检查:
  - Worker 心跳检测
  - 数据库连接检测
  - 代理池状态检测
  - 消息队列状态检测
  - 磁盘/内存资源检测
"""

import asyncio
import time
import platform
from typing import Callable, Optional


class HealthStatus:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth:
    """组件健康状态"""
    def __init__(self, name: str):
        self.name = name
        self.status = HealthStatus.HEALTHY
        self.last_check = 0.0
        self.last_error = ""
        self.consecutive_failures = 0
        self.latency_ms = 0.0

    def mark_healthy(self):
        self.status = HealthStatus.HEALTHY
        self.consecutive_failures = 0

    def mark_degraded(self, error: str = ""):
        self.status = HealthStatus.DEGRADED
        self.last_error = error

    def mark_unhealthy(self, error: str = ""):
        self.status = HealthStatus.UNHEALTHY
        self.consecutive_failures += 1
        self.last_error = error


class HealthChecker:
    """
    分布式健康检查器

    定期检查所有注册组件，维护健康状态。
    连续失败达到阈值 → 触发告警。
    """

    def __init__(self, check_interval: float = 30.0,
                 failure_threshold: int = 3):
        self.check_interval = check_interval
        self.failure_threshold = failure_threshold
        self._components: dict[str, tuple[Callable, ComponentHealth]] = {}
        self._running = False
        self.on_degraded: Optional[Callable] = None
        self.on_unhealthy: Optional[Callable] = None
        self.on_recovered: Optional[Callable] = None

    def register(self, name: str, check_func: Callable) -> None:
        """
        注册健康检查

        check_func 签名: async def check() -> dict
          返回: {"status": "healthy"/"degraded"/"unhealthy", "message": "...", ...}
        """
        self._components[name] = (check_func, ComponentHealth(name))

    async def check_component(self, name: str) -> ComponentHealth:
        """检查单个组件"""
        check_func, health = self._components[name]
        prev_status = health.status

        start = time.time()
        try:
            result = await check_func()
            health.latency_ms = (time.time() - start) * 1000

            status = result.get("status", "healthy")
            if status == "healthy":
                health.mark_healthy()
            elif status == "degraded":
                health.mark_degraded(result.get("message", ""))
            else:
                health.mark_unhealthy(result.get("message", ""))

        except Exception as e:
            health.latency_ms = (time.time() - start) * 1000
            health.mark_unhealthy(str(e))

        health.last_check = time.time()

        # 状态变化回调
        if health.status != prev_status:
            if health.status == HealthStatus.DEGRADED and self.on_degraded:
                self.on_degraded(name, health)
            elif health.status == HealthStatus.UNHEALTHY and self.on_unhealthy:
                self.on_unhealthy(name, health)
            elif health.status == HealthStatus.HEALTHY and self.on_recovered:
                self.on_recovered(name, health)

        return health

    async def check_all(self) -> dict[str, ComponentHealth]:
        """检查所有组件（并发）"""
        tasks = {name: self.check_component(name) for name in self._components}
        results = {}
        for name, result in tasks.items():
            results[name] = await result
        return results

    async def run_loop(self) -> None:
        """运行健康检查循环"""
        self._running = True
        while self._running:
            results = await self.check_all()
            unhealthy = [n for n, h in results.items() if h.status != HealthStatus.HEALTHY]
            if unhealthy:
                names = ", ".join(unhealthy)
                print(f"[HealthChecker] 不健康组件: {names}")
            await asyncio.sleep(self.check_interval)

    def stop(self):
        self._running = False

    def summary(self) -> dict:
        """生成健康摘要"""
        components = {}
        for name, (_, health) in self._components.items():
            components[name] = {
                "status": health.status,
                "last_check": health.last_check,
                "latency_ms": health.latency_ms,
                "consecutive_failures": health.consecutive_failures,
                "last_error": health.last_error,
            }
        overall = HealthStatus.HEALTHY
        if any(c["status"] == HealthStatus.UNHEALTHY for c in components.values()):
            overall = HealthStatus.UNHEALTHY
        elif any(c["status"] == HealthStatus.DEGRADED for c in components.values()):
            overall = HealthStatus.DEGRADED

        return {"overall": overall, "components": components}


# ---- 内置检查函数 ----

async def check_redis(redis_url: str = "redis://localhost:6379") -> dict:
    """检查 Redis 连接"""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, socket_connect_timeout=3)
        await r.ping()
        info = await r.info("memory")
        await r.close()
        return {"status": "healthy", "used_memory_mb": info.get("used_memory_human", "N/A")}
    except Exception as e:
        return {"status": "unhealthy", "message": str(e)}


async def check_mongodb(uri: str = "mongodb://localhost:27017") -> dict:
    """检查 MongoDB 连接"""
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=3000)
        await client.admin.command("ping")
        client.close()
        return {"status": "healthy"}
    except Exception as e:
        return {"status": "unhealthy", "message": str(e)}


async def check_disk(path: str = "/", min_free_gb: float = 5.0) -> dict:
    """检查磁盘空间"""
    try:
        import shutil
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        if free_gb < min_free_gb:
            return {"status": "degraded", "message": f"仅剩 {free_gb:.1f}GB",
                    "free_gb": free_gb}
        return {"status": "healthy", "free_gb": free_gb}
    except Exception as e:
        return {"status": "degraded", "message": str(e)}


async def check_memory(min_free_mb: float = 512) -> dict:
    """检查内存"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        free_mb = mem.available / (1024**2)
        if free_mb < min_free_mb:
            return {"status": "degraded", "message": f"仅剩 {free_mb:.0f}MB",
                    "free_mb": free_mb, "percent": mem.percent}
        return {"status": "healthy", "free_mb": free_mb, "percent": mem.percent}
    except ImportError:
        return {"status": "healthy", "message": "psutil 未安装，跳过内存检查"}

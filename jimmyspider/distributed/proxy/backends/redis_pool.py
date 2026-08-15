"""
Redis 代理池后端

基于 Redis List 存储代理，支持:
  - 多种评分策略（随机/最低延迟/最高成功率/加权）
  - 令牌桶限流
  - 自动标记失效代理

配置来源: jimmyspider 全局配置（REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB）
参考: jimmyspider/proxy.py ProxyManager + RedisTokenBucket
"""

import json
import random
import time
import asyncio
from typing import Optional

import redis.asyncio as aioredis

from ..base import ProxyBackend, ProxyInfo
from jimmyspider.config import get_config


class RedisPoolBackend(ProxyBackend):
    """
    Redis 代理池后端

    代理存储在 Redis List 中:
      proxy:pool → [{"host":"x","port":8080,...}, ...]
      proxy:dead → [死代理列表]
      proxy:stats:{host}:{port} → Hash (success_count, fail_count, latency)

    限流: Redis Token Bucket (Lua 脚本, 参考 jimmyspider/proxy.py)
    """

    backend_name = "redis_pool"

    LUA_TOKEN_BUCKET = """
    local key = KEYS[1]
    local rate = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local need = tonumber(ARGV[4])

    local data = redis.call("HMGET", key, "tokens", "timestamp")
    local tokens = tonumber(data[1])
    local timestamp = tonumber(data[2])

    if tokens == nil then
        tokens = capacity
        timestamp = now
    end

    local delta = math.max(0, now - timestamp)
    tokens = math.min(capacity, tokens + delta * rate)
    local allowed = tokens >= need

    if allowed then
        tokens = tokens - need
    end

    redis.call("HMSET", key, "tokens", tokens, "timestamp", now)
    return allowed
    """

    def __init__(self, redis_url: str = None,
                 pool_key: str = "proxy:list",
                 strategy: str = "weighted",  # random / lowest_latency / highest_success / weighted
                 rate: float = 5.0,
                 capacity: int = 10,
                 test_url: str = "https://www.baidu.com"):
        cfg = get_config()
        if redis_url is None:
            # 默认取全局配置（如未配置密码则不携带认证段）
            auth = f":{cfg.REDIS_PASSWORD}@" if cfg.REDIS_PASSWORD else ""
            redis_url = f"redis://{auth}{cfg.REDIS_HOST}:{cfg.REDIS_PORT}/{cfg.REDIS_DB}"
        self.redis_url = redis_url
        self.pool_key = pool_key
        self.dead_key = f"{pool_key}:dead"
        self.stats_prefix = f"{pool_key}:stats:"
        self.bucket_key = f"{pool_key}:bucket"
        self.strategy = strategy
        self.rate = rate
        self.capacity = capacity
        self.test_url = test_url
        self._redis: Optional[aioredis.Redis] = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
        return self._redis

    # ---- 获取代理 ----
    async def get_proxy(self, tags: list[str] = None) -> Optional[ProxyInfo]:
        r = await self._get_redis()

        # 令牌桶限流
        now = int(time.time())
        allowed = await r.eval(
            self.LUA_TOKEN_BUCKET, 1,
            self.bucket_key, self.rate, self.capacity, now, 1
        )
        if not allowed:
            return None

        # 获取代理池
        raw_list = await r.lrange(self.pool_key, 0, -1)
        if not raw_list:
            return None

        proxies = []
        for raw in raw_list:
            try:
                p = json.loads(raw)
                info = ProxyInfo(
                    host=p["host"], port=p["port"],
                    scheme=p.get("scheme", "http"),
                    username=p.get("username", ""),
                    password=p.get("password", ""),
                    source="redis_pool",
                    tags=p.get("tags", []),
                )
                # 读取统计
                stats_key = f"{self.stats_prefix}{info.host}:{info.port}"
                stats = await r.hgetall(stats_key)
                if stats:
                    info.success_count = int(stats.get("success_count", 0))
                    info.fail_count = int(stats.get("fail_count", 0))
                    info.latency_ms = float(stats.get("latency_ms", 0))
                proxies.append(info)
            except (json.JSONDecodeError, KeyError):
                continue

        if not proxies:
            return None

        # Tag 过滤
        if tags:
            proxies = [p for p in proxies if any(t in p.tags for t in tags)]
            if not proxies:
                return None

        # 按策略选择
        return self._select(proxies)

    def _select(self, proxies: list[ProxyInfo]) -> ProxyInfo:
        if self.strategy == "random":
            return random.choice(proxies)
        elif self.strategy == "lowest_latency":
            return min(proxies, key=lambda p: p.latency_ms if p.latency_ms > 0 else 9999)
        elif self.strategy == "highest_success":
            return max(proxies, key=lambda p: p.score)
        else:  # weighted
            total_score = sum(p.score for p in proxies)
            if total_score == 0:
                return random.choice(proxies)
            r = random.uniform(0, total_score)
            cumulative = 0
            for p in proxies:
                cumulative += p.score
                if r <= cumulative:
                    return p
            return proxies[-1]

    # ---- 报告 ----
    async def report_success(self, proxy: ProxyInfo) -> None:
        proxy.record_success()
        await self._update_stats(proxy)

    async def report_failure(self, proxy: ProxyInfo, error: str = "") -> None:
        proxy.record_failure()
        await self._update_stats(proxy)
        # 连续失败 3 次 → 移入死代理池
        if proxy.fail_count >= 3:
            await self._mark_dead(proxy)

    async def _update_stats(self, proxy: ProxyInfo) -> None:
        r = await self._get_redis()
        key = f"{self.stats_prefix}{proxy.host}:{proxy.port}"
        await r.hset(key, mapping={
            "success_count": proxy.success_count,
            "fail_count": proxy.fail_count,
            "latency_ms": proxy.latency_ms,
            "last_used": proxy.last_used,
        })

    async def _mark_dead(self, proxy: ProxyInfo) -> None:
        r = await self._get_redis()
        proxy.is_alive = False
        # 从活跃池移除
        await r.lrem(self.pool_key, 0, json.dumps({
            "host": proxy.host, "port": proxy.port,
            "scheme": proxy.scheme, "username": proxy.username,
            "password": proxy.password, "tags": proxy.tags,
        }))
        # 加入死池
        await r.sadd(self.dead_key, json.dumps({
            "host": proxy.host, "port": proxy.port, "error": "consecutive_failures",
            "fail_count": proxy.fail_count, "timestamp": time.time(),
        }))

    # ---- 健康检查 ----
    async def health_check(self) -> dict:
        r = await self._get_redis()
        total = await r.llen(self.pool_key)
        dead = await r.scard(self.dead_key)
        return {
            "backend": self.backend_name,
            "alive": total,
            "dead": dead,
            "ratio": total / max(total + dead, 1),
            "strategy": self.strategy,
        }

    async def get_stats(self) -> dict:
        return await self.health_check()

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

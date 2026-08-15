"""
分布式代理 — 使用示例

演示: Redis代理池 + Clash代理 + 隧道代理 的组合使用。

运行前提: 已安装 jimmySpider（或位于仓库根目录），敏感配置放入 jimmyspider.yaml /
环境变量（见 jimmyspider/config.py），后端默认值全部来自全局配置。
运行方式: python -m jimmyspider.distributed.examples.example_proxy
"""

import asyncio

from jimmyspider.distributed import DistributedProxyManager, ProxyBackend
from jimmyspider.distributed.proxy.backends import (
    RedisPoolBackend, ClashPoolBackend, TunnelAPIBackend,
)


async def main():
    # ---- 创建管理器 ----
    manager = DistributedProxyManager(strategy="primary")

    # 添加后端: 优先级 1 = 首选
    manager.add_backend(
        RedisPoolBackend(
            redis_url="redis://localhost:6379",
            pool_key="proxy:list",
            strategy="weighted",  # 按成功率加权选择
            rate=5.0, capacity=10,
        ),
        priority=1, weight=10,
    )

    # 添加后端: 优先级 2 = 备选 (Clash 节点池，默认取全局配置
    # CLASH_API_URL / CLASH_SECRET / CLASH_PROXY_URL / CLASH_POLICY_GROUP)
    manager.add_backend(
        ClashPoolBackend(
            switch_strategy="round_robin",
        ),
        priority=2, weight=5,
    )

    # 添加后端: 优先级 3 = 兜底 (隧道代理，默认取全局配置 PROXY_TUNNEL_URL)
    manager.add_backend(
        TunnelAPIBackend(mode="tunnel"),
        priority=3, weight=3,
    )

    # ---- 获取代理 ----
    print("=== 获取代理 ===")
    for i in range(5):
        proxy = await manager.get_proxy(tags=["domestic"])
        if proxy:
            print(f"  [{i+1}] {proxy.source}: {proxy.host}:{proxy.port} "
                  f"(score={proxy.score:.2f})")
            # 模拟使用
            await manager.report_success(proxy)
        else:
            print(f"  [{i+1}] 无可用代理")

    # ---- 健康检查 ----
    print("\n=== 健康检查 ===")
    hc = await manager.health_check()
    for backend, status in hc.items():
        print(f"  {backend}: {status}")

    # ---- 统计 ----
    stats = await manager.get_stats()
    print(f"\n=== 统计 ===")
    print(f"  总请求: {stats['total_requests']}")
    print(f"  成功率: {stats['success_rate']:.2%}")

    await manager.close()


if __name__ == "__main__":
    asyncio.run(main())

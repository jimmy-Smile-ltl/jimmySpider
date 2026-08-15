"""
分布式监控运维 — 使用示例

演示: 指标采集 + 健康检查 + 告警 + Dashboard 的完整监控链路。

运行前提: 已安装 jimmySpider（或位于仓库根目录）。
运行方式: python -m jimmyspider.distributed.examples.example_monitor
"""

import asyncio
import sys

# Windows GBK 控制台无法打印 emoji，统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from jimmyspider.distributed import (
    MetricsCollector, HealthChecker, AlertManager, DashboardExporter,
)
from jimmyspider.distributed.monitor.healthcheck import (
    check_redis, check_mongodb, check_disk, check_memory,
)
from jimmyspider.distributed.monitor.alerting import (
    AlertRule, WebhookChannel, ConsoleChannel,
)


async def main():
    # ---- 1. 指标采集器 ----
    metrics = MetricsCollector(namespace="spider")

    # ---- 2. 健康检查器 ----
    health = HealthChecker(check_interval=30.0, failure_threshold=3)

    # 注册检查项
    health.register("redis", lambda: check_redis("redis://localhost:6379"))
    health.register("mongodb", lambda: check_mongodb("mongodb://localhost:27017"))
    health.register("disk", lambda: check_disk("/", min_free_gb=5))
    health.register("memory", lambda: check_memory(min_free_mb=512))

    # 状态变化回调
    health.on_unhealthy = lambda name, h: print(f"🚨 {name} 异常: {h.last_error}")
    health.on_recovered = lambda name, h: print(f"✅ {name} 已恢复")

    # ---- 3. 告警管理器 ----
    alerts = AlertManager(min_severity="warning")

    # 添加规则
    alerts.add_rule(AlertManager.error_rate_rule(threshold=0.3))
    alerts.add_rule(AlertManager.queue_backlog_rule(max_depth=10000))
    alerts.add_rule(AlertManager.proxy_pool_low_rule(min_alive=3))

    # 添加通道
    alerts.add_channel(ConsoleChannel())
    # alerts.add_channel(WebhookChannel("wecom", "https://qyapi.weixin.qq.com/..."))

    # ---- 4. Dashboard 导出器 ----
    dashboard = DashboardExporter(metrics, health, alerts)

    # ---- 模拟 Worker 上报指标 ----
    print("=== 模拟 Worker 运行 ===")
    for i in range(20):
        metrics.record_request(
            worker_id="worker_1",
            category="个股研报",
            success=(i % 5 != 0),  # 每 5 个失败 1 个
            latency_ms=100 + (i * 10),
        )

    metrics.record_queue_depth("eastmoney:tasks", 42)
    metrics.record_proxy_pool(alive=8, dead=2, backend="redis_pool")
    metrics.record_db_connections("mongodb", active=5, idle=15)

    # ---- 5. 输出 ----
    print(f"\n=== 指标摘要 ===")
    summary = metrics.summary()
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print(f"\n=== Prometheus 格式 ===")
    print(metrics.prometheus_text()[:500] + "...")

    print(f"\n=== 健康检查 ===")
    health_results = await health.check_all()
    for name, h in health_results.items():
        print(f"  {name}: {h.status} ({h.latency_ms:.1f}ms)")

    print(f"\n=== 告警评估 ===")
    triggered = await alerts.evaluate(metrics)
    if triggered:
        for t in triggered:
            print(f"  [{t['severity']}] {t['rule']}: {t['message']}")
    else:
        print("  无告警触发")

    # ---- 启动 Dashboard HTTP ----
    # await dashboard.start_http_server(port=9090)
    # await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())

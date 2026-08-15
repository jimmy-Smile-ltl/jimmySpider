"""
monitor_demo —— jimmyspider.distributed.monitor 监控告警集成演示（完全离线）

演示分布式爬虫监控链路的三件套：
  1. MetricsCollector   指标采集：record_request(成功/失败/耗时) + 队列深度 + 代理池
  2. HealthChecker      健康检查：内置 check_disk / check_memory（psutil，离线可用）
                        + 自定义 worker 心跳检查（模拟故障触发回调）
  3. AlertManager       告警：ConsoleChannel（无需 webhook）+ 3 条内置规则
                        （错误率 / 队列堆积 / 代理池可用数），并演示冷却期抑制

运行: python spider.py          （零外部依赖，无网络、无 Redis、无 webhook）
"""

import argparse
import asyncio
import sys

# Windows GBK 控制台无法打印中文/emoji，统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from jimmyspider.distributed.monitor import (
    AlertManager, AlertRule, ConsoleChannel, HealthChecker, MetricsCollector,
)
from jimmyspider.distributed.monitor.healthcheck import check_disk, check_memory


class MetricsSpider:
    """
    模拟一个真实爬虫：抓取 40 个详情页，每第 3 个请求失败（错误率 ≈ 33%），
    每次请求都记录 成功/失败/耗时 指标。
    """

    def __init__(self, metrics: MetricsCollector, worker_id: str = "worker_1",
                 category: str = "个股研报", fail_every: int = 3):
        self.metrics = metrics
        self.worker_id = worker_id
        self.category = category
        self.fail_every = fail_every   # 每 N 个请求失败 1 个 → 错误率 1/N

    def crawl(self, total: int = 40):
        for i in range(1, total + 1):
            success = (i % self.fail_every != 0)
            latency_ms = 80 + (i * 7) % 200          # 80 ~ 280ms
            if not success:
                latency_ms = 400 + (i * 11) % 300    # 失败更慢
            self.metrics.record_request(
                worker_id=self.worker_id,
                category=self.category,
                success=success,
                latency_ms=latency_ms,
            )
            # 框架的 summary()/error_rate_rule 按「无 label」键聚合，
            # 这里同时维护汇总计数（带 label 的明细计数供 Prometheus 使用）
            self.metrics.incr("spider_requests_total")
            if success:
                self.metrics.incr("spider_requests_success")
            else:
                self.metrics.incr("spider_requests_failed")
            status = "OK" if success else "FAIL"
            print(f"  [Spider] {self.category} page/{i:02d} {status} {latency_ms}ms")


async def main(fail_every: int = 3, total: int = 40):
    print("=" * 64)
    print("1) 指标采集 MetricsCollector —— 模拟爬虫运行")
    print("=" * 64)
    metrics = MetricsCollector(namespace="spider")

    spider = MetricsSpider(metrics, fail_every=fail_every)
    spider.crawl(total=total)

    # 集群级指标
    metrics.record_queue_depth("eastmoney:tasks", 12345)        # 队列堆积
    metrics.record_proxy_pool(alive=1, dead=9, backend="redis_pool")  # 代理池枯竭
    metrics.record_db_connections("mongodb", active=5, idle=15)

    summary = metrics.summary()
    print(f"\n  摘要: 总请求 {summary['total_requests']} | "
          f"成功率 {summary['success_rate']:.1%} | "
          f"失败 {summary['total_failed']}")
    print(f"\n  Prometheus 格式输出（前 6 行）：")
    for line in metrics.prometheus_text().splitlines()[:6]:
        print(f"    {line}")

    print("\n" + "=" * 64)
    print("2) 健康检查 HealthChecker —— 磁盘 / 内存 / 自定义 worker 心跳")
    print("=" * 64)
    health = HealthChecker(check_interval=30.0, failure_threshold=2)

    # 内置检查（psutil，完全离线）；真实集群再加 check_redis / check_mongodb
    health.register("disk", lambda: check_disk("/", min_free_gb=5))
    health.register("memory", lambda: check_memory(min_free_mb=512))

    # 自定义检查：模拟 worker 心跳（连续检查 3 轮后宕机 → unhealthy）
    heartbeat = {"fail_count": 0}

    async def worker_heartbeat():
        if heartbeat["fail_count"] >= 2:
            return {"status": "unhealthy",
                    "message": "worker_1 心跳超时 60s，疑似宕机"}
        heartbeat["fail_count"] += 1
        return {"status": "healthy", "message": "心跳正常"}

    health.register("worker_heartbeat", worker_heartbeat)

    # 状态变化回调（可对接告警通道/日志）
    health.on_unhealthy = lambda name, h: print(f"  [Health] {name} 异常: {h.last_error}")
    health.on_recovered = lambda name, h: print(f"  [Health] {name} 已恢复")

    for round_no in range(1, 4):   # 连查 3 轮：worker 心跳从健康演变为宕机
        print(f"  ---- 第 {round_no} 轮检查 ----")
        results = await health.check_all()
        for name, h in results.items():
            print(f"  {name:<16} status={h.status:<10} latency={h.latency_ms:.1f}ms"
                  + (f" | {h.last_error}" if h.last_error else ""))

    print("\n" + "=" * 64)
    print("3) 告警 AlertManager —— ConsoleChannel + 内置规则")
    print("=" * 64)
    alerts = AlertManager(min_severity="warning")
    alerts.add_channel(ConsoleChannel())      # 打印到控制台，无需 webhook

    # 错误率规则：与 AlertManager.error_rate_rule() 相同的判定逻辑。
    # 注意：框架自带的 error_rate_rule 默认消息模板引用了未定义的 {threshold}
    # 占位符，触发时会抛 KeyError（被 AlertManager 静默吞掉），
    # 因此这里用等价的显式 AlertRule 演示（逻辑一致）。
    def error_rate_condition(m, threshold=0.30):
        s = m.summary()
        return s["total_requests"] > 10 and (1 - s["success_rate"]) > threshold

    alerts.add_rule(AlertRule(
        name="high_error_rate", condition=error_rate_condition,
        severity="critical", cooldown_seconds=30,
        message_template="错误率超过 30% 阈值（{value}）",
    ))
    alerts.add_rule(AlertManager.queue_backlog_rule(max_depth=10000, cooldown=1))
    alerts.add_rule(AlertManager.proxy_pool_low_rule(min_alive=3, cooldown=1))
    # 自定义规则示例：alerts.add_rule(AlertRule("custom", lambda m: ..., severity="warning"))

    print(f"  模拟错误率 {1 - summary['success_rate']:.1%}（阈值 30%），"
          f"队列深度 12345（阈值 10000），代理存活 1（阈值 3）→ 应触发 3 条告警\n")
    triggered = await alerts.evaluate(metrics)
    print(f"  本次触发 {len(triggered)} 条告警\n")

    # 冷却期演示：cooldown 内重复评估不会重复告警（防告警风暴）
    again = await alerts.evaluate(metrics)
    print(f"  冷却期内再次评估 → 触发 {len(again)} 条（全部被抑制）")

    # 故障恢复演示：30 个请求全部成功 → 错误率降回 30% 以下；
    # 等待 1s 后重新评估：队列/代理池规则（cooldown=1s）重新触发，
    # 错误率规则（cooldown=30s 且已恢复）不再触发
    for i in range(1, 31):
        metrics.record_request("worker_1", "个股研报", success=True, latency_ms=90)
        metrics.incr("spider_requests_total")
        metrics.incr("spider_requests_success")
    await asyncio.sleep(1.2)
    after_recovery = await alerts.evaluate(metrics)
    print(f"  错误率恢复后评估 → 触发 {len(after_recovery)} 条"
          f"（queue_backlog / proxy_pool_low 重新触发，error_rate 已恢复）")

    print("\n" + "=" * 64)
    print("4) 运维输出 —— 健康摘要 + 告警历史")
    print("=" * 64)
    hs = health.summary()
    print(f"  集群健康: {hs['overall']}")
    print(f"  告警历史: {len(alerts._alert_history)} 条")
    for a in alerts._alert_history:
        print(f"    [{a['severity']}] {a['rule']}: {a['message']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="jimmySpider 监控告警集成演示")
    parser.add_argument("--total", type=int, default=40, help="模拟请求总数")
    parser.add_argument("--fail-every", type=int, default=3,
                        help="每 N 个请求失败 1 个（默认 3 → 错误率 33%，超过 30% 阈值）")
    args = parser.parse_args()
    asyncio.run(main(fail_every=args.fail_every, total=args.total))

"""
分布式指标采集器

收集和聚合分布式爬虫集群的运行指标。
参考 jimmyspider/log_print.py 的日志设计 + Prometheus metrics 标准。

指标类型:
  - Counter: 累计计数（请求数、采集数、错误数）
  - Gauge: 瞬时值（队列长度、Worker 数、代理存活数）
  - Histogram: 分布统计（响应时间、页面大小）
"""

import time
import asyncio
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MetricSnapshot:
    """指标快照"""
    name: str
    value: float
    labels: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metric_type: str = "gauge"  # counter / gauge / histogram


class MetricsCollector:
    """
    分布式指标采集器

    采集维度:
      - Worker 级别: 每个 Worker 的吞吐、错误率、内存
      - Category 级别: 每个 category 的进度、页数
      - 全局: 队列长度、代理池状态、数据库连接

    存储后端: Redis (用于实时指标) + Prometheus (用于长期存储)
    """

    def __init__(self, namespace: str = "spider",
                 redis_url: str = "redis://localhost:6379"):
        self.namespace = namespace
        self.redis_url = redis_url
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self._start_time = time.time()

    # ---- Counter ----
    def incr(self, name: str, value: float = 1, labels: dict = None) -> None:
        """增加计数器"""
        key = self._metric_key(name, labels)
        with self._lock:
            self._counters[key] += value

    # ---- Gauge ----
    def set_gauge(self, name: str, value: float, labels: dict = None) -> None:
        """设置仪表值"""
        key = self._metric_key(name, labels)
        self._gauges[key] = value

    # ---- Histogram ----
    def observe(self, name: str, value: float, labels: dict = None) -> None:
        """记录直方图值"""
        key = self._metric_key(name, labels)
        with self._lock:
            self._histograms[key].append(value)

    # ---- 快照 ----
    def snapshot(self) -> list[MetricSnapshot]:
        """获取所有指标快照"""
        snapshots = []
        with self._lock:
            for key, val in self._counters.items():
                snapshots.append(MetricSnapshot(name=key, value=val, metric_type="counter"))
            for key, val in self._gauges.items():
                snapshots.append(MetricSnapshot(name=key, value=val, metric_type="gauge"))
            for key, vals in self._histograms.items():
                if vals:
                    snapshots.append(MetricSnapshot(
                        name=key, value=sum(vals) / len(vals),
                        metric_type="histogram"))
        return snapshots

    # ---- 爬虫专用指标 ----
    def record_request(self, worker_id: str, category: str, success: bool,
                       latency_ms: float = 0) -> None:
        """记录一次请求"""
        self.incr("spider_requests_total", labels={"worker": worker_id, "category": category})
        if success:
            self.incr("spider_requests_success", labels={"worker": worker_id, "category": category})
        else:
            self.incr("spider_requests_failed", labels={"worker": worker_id, "category": category})
        if latency_ms > 0:
            self.observe("spider_request_latency_ms", latency_ms,
                        labels={"worker": worker_id, "category": category})

    def record_queue_depth(self, queue_name: str, depth: int) -> None:
        self.set_gauge("spider_queue_depth", depth, labels={"queue": queue_name})

    def record_proxy_pool(self, alive: int, dead: int, backend: str = "") -> None:
        self.set_gauge("spider_proxy_alive", alive, labels={"backend": backend})
        self.set_gauge("spider_proxy_dead", dead, labels={"backend": backend})

    def record_db_connections(self, db_type: str, active: int, idle: int) -> None:
        self.set_gauge("spider_db_connections_active", active, labels={"db": db_type})
        self.set_gauge("spider_db_connections_idle", idle, labels={"db": db_type})

    # ---- 报告 ----
    def summary(self) -> dict:
        """生成摘要报告"""
        now = time.time()
        uptime = now - self._start_time
        total_req = self._counters.get(self._metric_key("spider_requests_total"), 0)
        success = self._counters.get(self._metric_key("spider_requests_success"), 0)
        failed = self._counters.get(self._metric_key("spider_requests_failed"), 0)

        return {
            "uptime_seconds": uptime,
            "total_requests": int(total_req),
            "success_rate": success / max(total_req, 1),
            "requests_per_second": total_req / max(uptime, 1),
            "total_success": int(success),
            "total_failed": int(failed),
        }

    # ---- Prometheus 格式输出 ----
    def prometheus_text(self) -> str:
        """导出为 Prometheus text 格式"""
        lines = []
        for snap in self.snapshot():
            label_str = ""
            if snap.labels:
                label_parts = [f'{k}="{v}"' for k, v in snap.labels.items()]
                label_str = "{" + ",".join(label_parts) + "}"
            metric_name = f"{self.namespace}_{snap.name}"
            lines.append(f"# TYPE {metric_name} {snap.metric_type}")
            lines.append(f"{metric_name}{label_str} {snap.value}")
        return "\n".join(lines)

    @staticmethod
    def _metric_key(name: str, labels: dict = None) -> str:
        if labels:
            label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
            return f"{name}:{label_str}"
        return name

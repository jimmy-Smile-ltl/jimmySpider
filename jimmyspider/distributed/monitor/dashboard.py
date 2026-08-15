"""
Prometheus / Grafana 仪表盘导出器

提供:
  - /metrics 端点 (Prometheus 格式)
  - /health 端点 (JSON 健康检查)
  - /stats 端点 (JSON 统计摘要)
  - Grafana Dashboard JSON 模板

使用:
    exporter = DashboardExporter(metrics, health_checker)
    await exporter.start_http_server(port=9090)
    # Prometheus: http://localhost:9090/metrics
"""

import json
import asyncio
from aiohttp import web


class DashboardExporter:
    """
    仪表盘导出器

    启动一个 HTTP 服务，暴露指标和健康检查端点。
    可在 Docker Compose 中与 Prometheus + Grafana 配合使用。
    """

    def __init__(self, metrics, health_checker,
                 alert_manager=None):
        self.metrics = metrics
        self.health_checker = health_checker
        self.alert_manager = alert_manager
        self._app = web.Application()
        self._setup_routes()
        self._runner = None

    def _setup_routes(self):
        self._app.router.add_get("/metrics", self._handle_metrics)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/stats", self._handle_stats)
        self._app.router.add_get("/alerts", self._handle_alerts)

    async def _handle_metrics(self, request):
        text = self.metrics.prometheus_text()
        return web.Response(text=text, content_type="text/plain; version=0.0.4")

    async def _handle_health(self, request):
        if self.health_checker:
            summary = self.health_checker.summary()
        else:
            summary = {"overall": "unknown"}
        return web.json_response(summary)

    async def _handle_stats(self, request):
        return web.json_response(self.metrics.summary())

    async def _handle_alerts(self, request):
        history = []
        if self.alert_manager:
            history = self.alert_manager._alert_history[-50:]  # 最近 50 条
        return web.json_response({"recent_alerts": history})

    async def start_http_server(self, host: str = "0.0.0.0", port: int = 9090):
        """启动 HTTP 服务"""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host, port)
        await site.start()
        print(f"[Dashboard] 指标服务: http://{host}:{port}")
        print(f"  Prometheus scrape: http://{host}:{port}/metrics")
        print(f"  Health check:      http://{host}:{port}/health")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()


# ---- Grafana Dashboard JSON 模板 ----

GRAFANA_DASHBOARD_JSON = """
{
  "title": "Spider Cluster Dashboard",
  "uid": "spider-cluster",
  "panels": [
    {
      "title": "Request Rate",
      "targets": [{"expr": "rate(spider_requests_total[1m])"}]
    },
    {
      "title": "Success Rate",
      "targets": [{"expr": "spider_requests_success / spider_requests_total"}]
    },
    {
      "title": "Queue Depth",
      "targets": [{"expr": "spider_queue_depth"}]
    },
    {
      "title": "Proxy Pool Status",
      "targets": [
        {"expr": "spider_proxy_alive", "legendFormat": "alive"},
        {"expr": "spider_proxy_dead", "legendFormat": "dead"}
      ]
    },
    {
      "title": "DB Connections",
      "targets": [
        {"expr": "spider_db_connections_active"},
        {"expr": "spider_db_connections_idle"}
      ]
    },
    {
      "title": "Request Latency (P50/P90/P99)",
      "targets": [{"expr": "histogram_quantile(0.50, spider_request_latency_ms)"},
                   {"expr": "histogram_quantile(0.90, spider_request_latency_ms)"},
                   {"expr": "histogram_quantile(0.99, spider_request_latency_ms)"}]
    }
  ]
}
"""

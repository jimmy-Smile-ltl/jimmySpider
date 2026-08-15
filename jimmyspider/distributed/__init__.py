"""
jimmySpider 分布式模块

由实验室原型 (spider research/爬虫架构/分布式/) 移植并适配到 jimmySpider 框架:

- proxy:    多后端代理池（Redis 池 / Clash 节点池 / 隧道代理 API），
            负载均衡 + 故障自动降级 + 全局质量追踪
- storage:  多数据库统一接口（MongoDB / PostgreSQL / MySQL / Elasticsearch），
            双写 / 读写分离 / 按表分片 / 数据迁移
- monitor:  指标采集（Prometheus 格式）+ 健康检查 + 多渠道告警 +
            Prometheus/Grafana 仪表盘导出

所有敏感配置（Clash 密钥、代理隧道账号密码等）统一来自 jimmyspider 全局配置，
见 jimmyspider/config.py 与 jimmyspider.yaml.example。

使用示例:
    from jimmyspider.distributed import (
        DistributedProxyManager, ClashPoolBackend,
        DistributedStorageManager, MongoDBBackend,
        MetricsCollector, HealthChecker, AlertManager, DashboardExporter,
    )

    manager = DistributedProxyManager(strategy="primary")
    manager.add_backend(ClashPoolBackend(), priority=1, weight=10)  # 配置驱动
    proxy = await manager.get_proxy()
"""

from jimmyspider.distributed.proxy import (
    DistributedProxyManager,
    ProxyBackend,
    ProxyInfo,
    RedisPoolBackend,
    ClashPoolBackend,
    TunnelAPIBackend,
)
from jimmyspider.distributed.storage import (
    DistributedStorageManager,
    StorageBackend,
    StorageRecord,
    MongoDBBackend,
    PostgreSQLBackend,
    MySQLBackend,
    ElasticsearchBackend,
)
from jimmyspider.distributed.monitor import (
    MetricsCollector,
    MetricSnapshot,
    HealthChecker,
    HealthStatus,
    ComponentHealth,
    AlertManager,
    AlertRule,
    WebhookChannel,
    EmailChannel,
    ConsoleChannel,
    DashboardExporter,
)

__all__ = [
    # --- 代理 ---
    "DistributedProxyManager",
    "ProxyBackend",
    "ProxyInfo",
    "RedisPoolBackend",
    "ClashPoolBackend",
    "TunnelAPIBackend",
    # --- 存储 ---
    "DistributedStorageManager",
    "StorageBackend",
    "StorageRecord",
    "MongoDBBackend",
    "PostgreSQLBackend",
    "MySQLBackend",
    "ElasticsearchBackend",
    # --- 监控 ---
    "MetricsCollector",
    "MetricSnapshot",
    "HealthChecker",
    "HealthStatus",
    "ComponentHealth",
    "AlertManager",
    "AlertRule",
    "WebhookChannel",
    "EmailChannel",
    "ConsoleChannel",
    "DashboardExporter",
]

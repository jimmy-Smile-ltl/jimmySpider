# jimmySpider 分布式模块 — 代理 / 存储 / 监控运维

将实验室原型（`spider research/爬虫架构/分布式/`）移植到 jimmySpider 框架：单机组件升级为分布式架构，提供统一抽象层，兼容多种后端。所有敏感配置（Clash 密钥、隧道代理账号密码等）统一来自 jimmySpider 全局配置（`jimmyspider/config.py` 或 `jimmyspider.yaml`），代码内不含任何硬编码凭据。

## 设计理念

```
┌──────────────────────────────────────────────────────────┐
│                jimmySpider Distributed                     │
│                                                           │
│  ┌────────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ Distributed    │  │ Distributed  │  │ Distributed  │ │
│  │ Proxy Manager  │  │Storage Mgr   │  │ Monitor      │ │
│  │                │  │              │  │              │ │
│  │ ┌────────────┐ │  │ ┌──────────┐ │  │ ┌──────────┐ │ │
│  │ │Redis Pool  │ │  │ │ MongoDB  │ │  │ │ Metrics  │ │ │
│  │ │Clash Pool  │ │  │ │PostgreSQL│ │  │ │HealthChk │ │ │
│  │ │Tunnel API  │ │  │ │  MySQL   │ │  │ │ Alerting │ │ │
│  │ └────────────┘ │  │ │   ES     │ │  │ │Dashboard │ │ │
│  └────────────────┘  │ └──────────┘ │  │ └──────────┘ │ │
│                       └──────────────┘  └──────────────┘ │
│                                                           │
│  统一接口 → 任意切换后端 → 无痛升级                         │
└──────────────────────────────────────────────────────────┘
```

## 目录结构

```
jimmyspider/distributed/
├── __init__.py                            # 统一导出全部类
├── README.md                              # 本文件
├── proxy/                                 # 分布式代理
│   ├── base.py                            # ProxyBackend 抽象接口 + ProxyInfo
│   ├── manager.py                         # DistributedProxyManager
│   └── backends/
│       ├── redis_pool.py                  # Redis代理池 (加权/随机/低延迟)
│       ├── clash_pool.py                  # Clash节点池 (轮询/自动切换)
│       └── tunnel_api.py                  # 隧道代理 (tunnel/api双模式)
├── storage/                               # 分布式存储
│   ├── base.py                            # StorageBackend 抽象接口
│   ├── manager.py                         # DistributedStorageManager
│   └── backends/
│       ├── mongodb.py                     # MongoDB (motor 异步驱动)
│       ├── postgresql.py                  # PostgreSQL (asyncpg + JSONB)
│       ├── mysql.py                       # MySQL (aiomysql + JSON)
│       └── elasticsearch.py               # Elasticsearch (全文搜索)
├── monitor/                               # 分布式监控运维
│   ├── metrics.py                         # 指标采集 (Counter/Gauge/Histogram)
│   ├── healthcheck.py                     # 健康检查 (组件+资源)
│   ├── alerting.py                        # 告警系统 (Webhook/Email/Console)
│   └── dashboard.py                       # Prometheus/Grafana 导出器
└── examples/                              # 使用示例
    ├── example_proxy.py
    ├── example_storage.py
    └── example_monitor.py
```

## 与框架现有组件的对应关系

| 现有组件 (单机) | 分布式升级 | 新增能力 |
|------|-----------|---------|
| `jimmyspider/proxy.py` ProxyManager | `proxy/` | 多后端负载均衡、自动降级、健康检查 |
| `jimmyspider/proxy_clash.py` ClashManager | `proxy/backends/clash_pool.py` | 异步API、节点评分、自动切换 |
| `jimmyspider/cache.py` Cache | 保留 (Redis 已天然分布式) | — |
| `jimmyspider/mongo.py` HandleMongoDB | `storage/backends/mongodb.py` | 异步驱动、连接池、自动重连 |
| — (无) | `storage/backends/postgresql.py` | JSONB存储、事务 |
| — (无) | `storage/backends/mysql.py` | JSON字段、读写分离 |
| — (无) | `storage/backends/elasticsearch.py` | 全文搜索 |
| `jimmyspider/log_print.py` LogPrint | `monitor/metrics.py` | Prometheus指标、聚合统计 |
| — (无) | `monitor/healthcheck.py` | 组件健康检测 |
| — (无) | `monitor/alerting.py` | 多渠道告警 |
| — (无) | `monitor/dashboard.py` | Grafana仪表盘 |

## 配置（敏感信息全部走全局配置）

在 `jimmyspider.yaml` 或环境变量中配置（详见 `jimmyspider.yaml.example`）：

| 配置键 | 环境变量 | 用途 |
|--------|---------|------|
| `proxy_tunnel_url` | `JIMMYSPIDER_PROXY_TUNNEL_URL` | 隧道代理完整地址 `http://user:pass@host:port` |
| `proxy_api_url` | `JIMMYSPIDER_PROXY_API_URL` | 代理 API 提取完整地址（含鉴权参数） |
| `clash_api_url` | `JIMMYSPIDER_CLASH_API_URL` | Clash REST API 地址 |
| `clash_secret` | `JIMMYSPIDER_CLASH_SECRET` | Clash API 密钥 |
| `clash_proxy_url` | `JIMMYSPIDER_CLASH_PROXY_URL` | Clash 本地代理出口 |
| `clash_policy_group` | `JIMMYSPIDER_CLASH_POLICY_GROUP` | Clash 策略组名称 |
| `redis_host/port/password/db` | `JIMMYSPIDER_REDIS_*` | Redis 代理池与缓存 |
| `mongo_uri` / `mongo_db` | `JIMMYSPIDER_MONGO_*` | MongoDB 存储 |
| `mysql_*` / `pg_*` | `JIMMYSPIDER_MYSQL_*` / `JIMMYSPIDER_PG_*` | MySQL / PostgreSQL 存储 |

后端构造时「显式参数 > 全局配置 > 默认值」，不传参即全部走配置，例如 `ClashPoolBackend()`。

## 代理 — 多后端组合

```python
from jimmyspider.distributed import DistributedProxyManager
from jimmyspider.distributed.proxy.backends import (
    RedisPoolBackend, ClashPoolBackend, TunnelAPIBackend,
)

# 创建管理器 (优先使用高优先级后端)
manager = DistributedProxyManager(strategy="primary")

# 主后端: Redis 代理池 (加权选择高成功率代理)
manager.add_backend(RedisPoolBackend(
    strategy="weighted", rate=5.0, capacity=10
), priority=1, weight=10)

# 备后端: Clash 节点池 (API 地址/密钥/策略组全部取自全局配置)
manager.add_backend(ClashPoolBackend(), priority=2, weight=5)

# 兜底: 隧道代理 (地址取自全局配置 PROXY_TUNNEL_URL)
manager.add_backend(TunnelAPIBackend(mode="tunnel"), priority=3, weight=3)

# 获取代理
proxy = await manager.get_proxy(tags=["domestic"])
await manager.report_success(proxy)  # 标记成功
await manager.report_failure(proxy, "timeout")  # 标记失败 → 自动降级
```

**4 种选择策略**: `primary` (优先级) / `fallback` (故障转移) / `round_robin` (轮询) / `weighted` (加权随机)

## 存储 — 多数据库兼容

```python
from jimmyspider.distributed import DistributedStorageManager
from jimmyspider.distributed.storage.backends import MongoDBBackend, PostgreSQLBackend

# 双写策略: 主 MongoDB + 备份 PostgreSQL
mgr = DistributedStorageManager(strategy="dual_write")
mgr.set_primary(MongoDBBackend())          # 连接串默认取全局配置
mgr.set_backup(PostgreSQLBackend())        # dsn 默认按全局配置 PG_* 拼接

# 统一接口 — 自动双写
await mgr.insert_one("reports", {"_id": "001", "title": "研报", ...})
await mgr.insert_many("reports", [...])
await mgr.find_one("reports", {"_id": "001"})
await mgr.update_one("reports", {"_id": "001"}, {"rating": "买入"})
await mgr.count("reports")
```

**4 种存储策略**:
| 策略 | 写入 | 读取 | 适用 |
|------|------|------|------|
| `primary_only` | 主后端 | 主后端 | 单数据库 |
| `dual_write` | 主+备份 | 主 | 灾备 |
| `read_write_split` | 主 | 从库(可多个) | 读写分离 |
| `shard_by_collection` | 按表分流 | 按表分流 | 混合存储 |

## 监控运维 — 完整链路

```python
from jimmyspider.distributed import (
    MetricsCollector, HealthChecker, AlertManager, DashboardExporter,
)
from jimmyspider.distributed.monitor.healthcheck import (
    check_redis, check_mongodb, check_disk, check_memory,
)
from jimmyspider.distributed.monitor.alerting import WebhookChannel

# 1. 指标采集
metrics = MetricsCollector(namespace="spider")
metrics.record_request("worker_1", "个股研报", success=True, latency_ms=150)
metrics.record_queue_depth("tasks", 42)
metrics.record_proxy_pool(alive=8, dead=2)

# 2. 健康检查
health = HealthChecker()
health.register("redis", lambda: check_redis("redis://localhost:6379"))
health.register("mongo", lambda: check_mongodb("mongodb://localhost:27017"))

# 3. 告警
alerts = AlertManager()
alerts.add_rule(AlertManager.error_rate_rule(threshold=0.3))
alerts.add_channel(WebhookChannel("wecom", "https://qyapi.weixin.qq.com/..."))

# 4. Dashboard (Prometheus + Grafana)
dashboard = DashboardExporter(metrics, health, alerts)
await dashboard.start_http_server(port=9090)
# → Prometheus scrape → Grafana visualize
```

## 集成示例: Worker 完整调用链

```python
class DistributedWorker:
    def __init__(self):
        self.proxy = DistributedProxyManager(strategy="primary")
        self.storage = DistributedStorageManager(strategy="dual_write")
        self.metrics = MetricsCollector(namespace="spider")

    async def crawl_page(self, task: dict) -> bool:
        # 1. 获取代理
        proxy = await self.proxy.get_proxy()
        if not proxy:
            return False

        # 2. 发送请求
        start = time.time()
        try:
            response = await self.fetch(task["url"], proxy=proxy.proxy_dict)
            self.metrics.record_request(
                self.worker_id, task["category"], success=True,
                latency_ms=(time.time() - start) * 1000
            )
            await self.proxy.report_success(proxy)
        except Exception as e:
            self.metrics.record_request(
                self.worker_id, task["category"], success=False
            )
            await self.proxy.report_failure(proxy, str(e))
            return False

        # 3. 储存结果
        await self.storage.insert_one("reports", response["data"])
        return True
```

## 运行示例

```bash
# 方式一: 已 pip install 后直接运行
python -m jimmyspider.distributed.examples.example_proxy
python -m jimmyspider.distributed.examples.example_storage
python -m jimmyspider.distributed.examples.example_monitor

# 方式二: 仓库根目录下直接运行
python jimmyspider/distributed/examples/example_proxy.py
```

## 选型建议

| 场景 | 代理 | 存储 | 监控 |
|------|------|------|------|
| 小规模 (<10万/天) | Redis代理池 | MongoDB | 控制台日志 |
| 中规模 (10-50万/天) | Redis + Clash | MongoDB + PG双写 | Prometheus + 企业微信告警 |
| 大规模 (>100万/天) | 多后端故障转移 | MongoDB写 + MySQL读分离 | Prometheus + Grafana + 多渠道告警 |
| 全文搜索需求 | — | MongoDB + Elasticsearch | — |
| 严格事务需求 | — | PostgreSQL | — |

## 可选依赖

存储与部分检查需要额外驱动（未安装时仅对应后端不可用，不影响整体导入）：

```bash
pip install motor          # MongoDB 异步驱动
pip install asyncpg        # PostgreSQL 异步驱动
pip install aiomysql       # MySQL 异步驱动
pip install elasticsearch  # Elasticsearch 客户端
pip install psutil         # 内存健康检查
```

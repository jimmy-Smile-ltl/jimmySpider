# 分布式架构指南

jimmySpider 从单机爬虫起步，内置了完整的分布式演进路径。本文档介绍框架的四个分布式子系统。

## 架构总览

```
单机模式（默认）                    分布式模式（可选）
───────────────                    ────────────────
JimmySpider                        MQ 生产者
  ├─ RequestHandler   ←────────    │  TaskMessage 入队
  ├─ MongoDB/MySQL/PG               ▼
  ├─ Redis 断点             ┌───────────────┐
  └─ Clash 代理             │  消息队列      │ ← jimmyspider.mq
                            │ Redis/Kafka/  │   (Redis 默认)
                            │ RabbitMQ      │
                            └──────┬────────┘
                                   ▼
                            MQ 消费者 × N 台
                              └─ JimmySpider 实例
                                   │
                     ┌─────────────┼─────────────┐
                     ▼             ▼             ▼
            distributed.proxy  distributed.storage  distributed.monitor
            (多后端代理池)      (双写/读写分离)       (指标/告警/看板)
```

## 1. 消息队列 `jimmyspider.mq`

统一接口，三种后端可互换：

```python
from jimmyspider.mq import RedisProducer, RedisConsumer, TaskMessage

# 生产者（列表页爬虫）
producer = RedisProducer("crawl_urls")
producer.send(TaskMessage(
    task_id="url_123",
    task_type="crawl_detail",
    payload={"url": "https://example.com/article/1"},
    priority=5,
))

# 消费者（详情页爬虫，可部署多台）
consumer = RedisConsumer("crawl_urls", handler=handle_detail)

def handle_detail(task: TaskMessage) -> bool:
    # 爬取详情页...
    return True  # 返回 True = ACK，False = NACK 进重试链

consumer.consume()
```

### 内置能力

| 能力 | 说明 |
|------|------|
| 优先级队列 | 0-9 级，ZSET 实现 |
| 延迟重试 | 失败指数退避（2^n 秒） |
| 死信队列 | 重试耗尽进入 `{topic}_dead`，可重放 |
| 断线恢复 | Stream 模式 XCLAIM 认领超时消息 |
| 批量发送 | 异步批量，Kafka 支持 per-key 分区 |

### 选型（研究结论）

| 场景 | 推荐 | 理由 |
|------|------|------|
| 中小规模（默认） | **Redis** | 数据结构驱动，零额外部署 |
| 复杂路由（topic 通配） | RabbitMQ | 4 种 Exchange + DLX 延迟 |
| 海量吞吐 | Kafka | 日志驱动，分区并行 |

## 2. 调度引擎 `jimmyspider.scheduler`

完整引擎（Engine/Scheduler/Downloader/Middleware/Signals 五层），两种风格：

```python
# AioSpider 风格（推荐：asyncio，实测吞吐高 2.4 倍）
from jimmyspider.scheduler import AioSpiderEngine, Request

class MySpider(BaseSpider):
    name = "my_spider"
    def start_requests(self):
        yield Request("https://example.com/page/1")
    def parse(self, response):
        # 解析列表，yield Request 继续爬
        yield Request("https://example.com/detail/1", callback="parse_detail")

engine = AioSpiderEngine(MySpider(), concurrent=50)
engine.run()
```

### 关键组件

| 组件 | 位置 | 用途 |
|------|------|------|
| `RFPDupeFilter` | `jimmyspider.request` | SHA1 指纹去重（也可在调度器内用） |
| `DomainRateLimiter` | `jimmyspider.request` | 按域名限速，防封 IP |
| `ScrapyEngine` | `jimmyspider.scheduler.scrapy_sched` | 线程模型，磁盘溢出 |
| `AioSpiderEngine` | `jimmyspider.scheduler.aiospider_sched` | asyncio 模型，背压控制 |

## 3. 智能解析 `jimmyspider.parser`

5 层成本级联，LLM 兜底，基于 1035 个真实站点数据：

```
L0 已知选择器 (sites.yaml, 成本 0, 置信度 1.0)
  ↓ 未命中
L1 语义 CSS 选择器 (~96% 覆盖)
  ↓ 未命中
L2 结构化数据 (JSON-LD / OG meta / title 标签)
  ↓ 未命中
L3 DOM 文本密度 (标题长度/链接比/语义类名加权)
  ↓ 仍未通过验证
L4 LLM 兜底（成本最高，结果缓存后不再调用）
```

```python
from jimmyspider.parser import TitleExtractor, ContentExtractor

title_ext = TitleExtractor()
title = title_ext.extract_title(html, url)

content_ext = ContentExtractor()
content = content_ext.extract_content(html, url)
```

**成本数据**（Claude Haiku 计价）：10 万页全 LLM ≈ $24.00，级联+缓存 ≈ $0.01，**省 99.96%**。

## 4. 分布式代理/存储/监控 `jimmyspider.distributed`

### 4.1 多后端代理

```python
from jimmyspider.distributed import DistributedProxyManager
from jimmyspider.distributed.proxy.backends import RedisPoolBackend, ClashPoolBackend

manager = DistributedProxyManager(strategy="weighted")
manager.add_backend(RedisPoolBackend(...), priority=1, weight=10)
manager.add_backend(ClashPoolBackend(), priority=2, weight=5)

proxy = manager.get_proxy()      # 按策略取代理
manager.report_success(proxy)    # 上报成功
manager.report_failure(proxy)    # 失败 5 次自动摘除
```

### 4.2 分布式存储

```python
from jimmyspider.distributed import DistributedStorageManager

storage = DistributedStorageManager(strategy="dual_write")
storage.add_backend(mongodb_backend, primary=True)
storage.add_backend(pg_backend)   # 双写备份

storage.insert_one({"url": "...", "title": "..."})
# read_write_split: 读走备份，写走主库
# shard_by_collection: 按 collection 分流
```

### 4.3 监控告警

```python
from jimmyspider.distributed import MetricsCollector, HealthChecker, AlertManager

metrics = MetricsCollector()
metrics.record_request("crawl_list", success=True, latency_ms=230)

health = HealthChecker()
health.register("redis", check_redis)
health.check_all()  # healthy/degraded/unhealthy 状态机

alerts = AlertManager()
alerts.add_rule(error_rate_rule(0.3))          # 错误率 > 30% 告警
alerts.add_channel(WebhookChannel(webhook_url)) # 企微/钉钉/飞书/Slack
```

## 演进路线

| 阶段 | 规模 | 架构 |
|------|------|------|
| v1.0（当前） | 单机，< 10 万页/天 | JimmySpider + 三数据库 + 断点续爬 |
| v1.5 | 单机多进程 | MQ（Redis）+ 列表/详情分离 |
| v2.0 | 多机 | MQ + distributed.proxy/storage/monitor |
| v3.0 | 大规模集群 | Kafka + 调度引擎 + Grafana 看板 |

> 详细研究数据和基准测试见 `spider research/爬虫架构/` 原始实验室。

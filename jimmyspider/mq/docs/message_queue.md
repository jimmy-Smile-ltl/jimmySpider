# 消息队列 — jimmyspider.mq 组件

三种消息队列（Redis / Kafka / RabbitMQ）的完整实现，专为爬虫任务分发设计。
本模块从 research lab 的 `message_queue` 项目移植，已适配 jimmySpider 框架
（包路径 `jimmyspider.mq`，Redis 连接默认读取框架配置）。

## 目录结构

```
jimmyspider/mq/
├── __init__.py                  # 统一出口（TaskMessage / 各 Producer / Consumer）
├── common/                      # 公共基类
│   ├── __init__.py
│   └── base.py                  # MessageQueueProducer / Consumer / TaskMessage
├── redis_mq/                    # Redis 消息队列
│   ├── __init__.py
│   ├── producer.py              # List / Stream / Priority / Delayed
│   └── consumer.py              # 三种消费模式 + 重试 + 死信
├── kafka_mq/                    # Kafka 消息队列
│   ├── __init__.py
│   ├── producer.py              # 同步/异步/Key分区/批量
│   └── consumer.py              # 消费者组/手动offset
├── rabbitmq_mq/                 # RabbitMQ 消息队列
│   ├── __init__.py
│   ├── producer.py              # Exchange路由/延迟/发布确认
│   └── consumer.py              # 手动ACK/QoS/死信
├── analysis/
│   └── benchmark_analysis.py    # 模拟测试 + 深度分析脚本（参考保留）
└── docs/
    ├── message_queue.md         # 本文件
    └── ANALYSIS_REPORT.md       # 原项目测试/分析/对比报告
```

## 快速开始

### 1. 安装依赖

```bash
pip install jimmyspider             # 含 redis（框架核心依赖）

# 按需安装其它 MQ 客户端
pip install kafka-python            # Kafka（可选）
pip install pika                    # RabbitMQ（可选）
```

### 2. 运行分析脚本（无需任何 MQ 服务）

```bash
# 纯逻辑模拟测试 + 深度分析 + 量化对比（11 组测试）
python jimmyspider/mq/analysis/benchmark_analysis.py
```

## 统一接口

所有三种 MQ 实现共享相同的基类接口，可以无痛切换：

```python
from jimmyspider.mq import TaskMessage
# 切换只需改 import
from jimmyspider.mq import RedisProducer as Producer
# from jimmyspider.mq import KafkaProducer as Producer
# from jimmyspider.mq import RabbitMQProducer as Producer

# 生产者 — 三种 MQ 用法完全一致
producer = Producer()
producer.connect()
task = TaskMessage(task_id="001", task_type="crawl",
                   payload={"url": "https://example.com"})
producer.send("spider_tasks", task)
producer.close()

# 消费者 — 三种 MQ 用法完全一致
def handle(task: TaskMessage) -> bool:
    return do_crawl(task)  # 返回 True=成功, False=失败

consumer = Consumer()
consumer.consume("spider_tasks", handle)
```

> 注意: Redis 生产者的 host/port/db/password 默认读取 jimmyspider 配置
> （`REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD`，
> 见 `jimmyspider.config.get_config()`），构造函数显式传入可覆盖。

## 功能矩阵

| 功能 | Redis | Kafka | RabbitMQ |
|------|:-----:|:-----:|:--------:|
| 基本队列 (FIFO) | ✅ List | ✅ Topic | ✅ Queue |
| 消费者组 | ✅ Stream | ✅ Group | ❌ |
| 消息确认 (ACK) | ✅ Stream | ✅ Offset | ✅ basic_ack |
| 消息持久化 | ✅ AOF/RDB | ✅ 磁盘 | ✅ durable |
| 消息回溯 | ✅ Stream | ✅ Offset | ❌ |
| 优先级队列 | ✅ ZSET | ❌ | ✅ 插件 |
| 延迟队列 | ✅ ZSET | ❌ | ✅ TTL+DLX |
| 批量发送 | ✅ | ✅ | ❌ (逐个) |
| 死信队列 | ✅ List | ❌ | ✅ DLX |
| 死信重放 | ✅ | ❌ | ✅ |
| 灵活路由 | ❌ | ❌ | ✅ 4种Exchange |
| 分区有序 | ❌ | ✅ Key分区 | ❌ |
| 管理界面 | ❌ | ❌ | ✅ Web UI |

## 选型指南

```
你的场景:
├── 已在用 Redis，不想加新组件
│   └── → Redis MQ（零额外成本）
│
├── 需要延迟队列 / 优先级队列
│   └── → Redis MQ 或 RabbitMQ（TTL+DLX）
│
├── 需要灵活路由（按 URL 类型/域名分流）
│   └── → RabbitMQ（Topic Exchange）
│
├── 消息量巨大 (百万级/天)，需要持久化和回溯
│   └── → Kafka（高吞吐 + 磁盘存储）
│
├── 需要严格顺序消费（同一网站的消息有序）
│   └── → Kafka（Key-based 分区）
│
├── 中小规模，希望有管理界面
│   └── → RabbitMQ（Management UI）
│
└── 简单任务队列，3 个 Worker 以内
    └── → Redis MQ（List 模式，最简单）
```

## 爬虫集成示例

### 最小化接入（Redis List）

```python
from jimmyspider.mq import RedisProducer, RedisConsumer, TaskMessage

producer = RedisProducer(mode="stream")  # host/port 自动取框架配置
producer.connect()

def distribute_urls(urls):
    for url in urls:
        task = TaskMessage(task_id=md5(url), task_type="crawl_detail",
                          payload={"url": url})
        producer.send("spider_detail_tasks", task)

# 另一进程并行消费
consumer = RedisConsumer(mode="stream", consumer_group="workers",
                         consumer_name="worker_1")
consumer.consume("spider_detail_tasks", crawl_and_save)
```

## 事件端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Redis | 6379 | 数据服务 |
| Kafka | 9092 | Broker (KRaft模式) |
| RabbitMQ AMQP | 5672 | 消息协议 |
| RabbitMQ Management | 15672 | Web UI (admin/admin) |

## 注意事项

1. **测试前确保 MQ 服务已启动**，Kafka 首次启动需要约 10-20 秒就绪
2. **Kafka 的 Topic 需要预先创建**（或启用自动创建 `auto.create.topics.enable=true`）
3. **RabbitMQ 的 Exchange/Queue 由代码自动声明**，无需手动创建
4. **Redis 测试会清空 `test_*` 前缀的 key**
5. **所有 MQ 的生产者/消费者都支持 `with` 上下文管理器**

## 参考链接

- [Redis Streams 官方文档](https://redis.io/docs/latest/develop/data-types/streams/)
- [Kafka 官方文档](https://kafka.apache.org/documentation/)
- [RabbitMQ 教程](https://www.rabbitmq.com/tutorials)
- [pika 文档](https://pika.readthedocs.io/)
- [kafka-python 文档](https://kafka-python.readthedocs.io/)

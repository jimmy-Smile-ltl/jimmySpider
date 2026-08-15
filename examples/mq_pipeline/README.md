# mq_pipeline —— 消息队列「列表/详情分离」流水线

> 演示 jimmySpider 经典拆分解耦模式：**列表爬虫只负责找 URL，详情爬虫只负责抓详情**，
> 中间用消息队列衔接。两端可独立扩缩容、跨机器部署，是分布式爬虫的骨架模式。

## 架构

```
列表页 ─→ producer.py（抓列表 → 解析出详情链接 → 封装 TaskMessage）
                    │ LPUSH 投递
                    ▼
             消息队列 mq_pipeline:tasks（主队列，Redis List）
                    │        ├─ tasks:retry_zset（重试队列，指数退避）
                    │        └─ tasks_dead（死信队列）
                    ▼ BRPOP 消费
        consumer.py（抓详情 → 解析 → 入库 SQLite/MongoDB）
```

## 运行方式

```bash
# 离线演示（默认，无需 Redis / 网络 / 数据库）：内存队列 + mock 列表页 + SQLite
python producer.py -m mock     # 生产者视角：投递后进程内跑完整流水线
python consumer.py -m mock     # 消费者视角：自动注入演示任务并消费

# 真实模式（需要 Redis）：producer / consumer 是两个独立进程
python producer.py -m redis    # 终端 1：抓列表 → LPUSH 投递
python consumer.py -m redis    # 终端 2：BRPOP 消费 → 详情入库
```

mock 模式故意模拟两类失败任务：`/detail/3` 第一次失败后重试成功（网络抖动），
`/detail/4` 永远失败进入死信 —— 用于观察重试与死信链路。

## 它演示了什么

1. **`TaskMessage` 统一消息体**：task_id / payload / priority / max_retries / retry_count，
   `to_json()` / `from_json()` 序列化
2. **`RedisProducer(mode="list")`**：LPUSH 入队 FIFO；另有 `stream`（XADD 消费者组）、
   `priority`（ZSET）、`send_delayed()`（延迟队列）
3. **`RedisConsumer(mode="list")`**：BRPOP 阻塞消费；失败自动指数退避重试（2^n 秒），
   超限进死信 `{topic}_dead`，可用 `replay_dead_letter()` 重放
4. **入库**：演示用 SQLite（stdlib）；真实项目换 `jimmyspider.mongo.HandleMongoDB`，
   `_id = generate_string_id(url)`

## 如何横向扩容

- 队列天然负载均衡：**同队列多开 N 个 `consumer.py` 进程（不同机器）即 N 倍吞吐**，
  无需改代码；列表/详情算力不对称时两端各自独立扩缩容
- 换 Kafka/RabbitMQ：接口完全一致（`KafkaProducer` / `RabbitMQProducer`），只改 import；
  Stream 模式自带消费者组，天然支持多 worker 分片
- 断点续爬：producer 侧用 `jimmyspider.cache.Cache` 记录已抓列表页；死信即「重试清单」

## 生产注意

- `mode="stream"` 更稳（持久化 + 回溯 + 消费者组）；`list` 简单但消息丢失不回溯
- 处理函数必须**幂等**（消息可能被重试多次）：入库用 INSERT OR REPLACE
- `handler` 返回 `False` 才触发重试/死信；抛异常视为失败

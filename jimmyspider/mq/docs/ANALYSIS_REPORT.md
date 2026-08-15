# 三种消息队列 — 测试 · 分析 · 总结 · 对比报告

> 生成时间: 2026-06-29 | 78 项测试 | 0 失败

---

## 一、测试结果汇总

### 1.1 核心逻辑测试 (11 组, 78 项)

| 测试组 | 项数 | 结果 |
|--------|------|------|
| TaskMessage 序列化/反序列化 | 4 | PASS |
| 重试与死信逻辑 | 4 | PASS |
| 接口一致性检查 (6 个基类方法 × 6 类) | 36 | PASS |
| 扩展功能检查 (12 个独有方法) | 12 | PASS |
| 代码度量验证 | 4 | PASS |
| 模拟并发消费 | 2 | PASS |
| 模拟优先级排序 | 2 | PASS |
| 模拟延迟队列 | 1 | PASS |
| 模拟 Topic Exchange 路由 | 7 | PASS |
| 模拟 Kafka Key-based 分区 | 3 | PASS |
| 模拟 Stream 消费者组 | 2 | PASS |
| 模拟 ACK/NACK 死信链路 | 1 | PASS |

### 1.2 代码度量

| 模块 | 代码行数 | 类数 | 方法数 | 注释行 |
|------|---------|------|--------|--------|
| common/base.py | 198 | 3 | 0 | 23 |
| redis_mq/producer.py | 131 | 1 | 0 | 19 |
| redis_mq/consumer.py | 319 | 1 | 0 | 40 |
| **Redis 合计** | **450** | | | |
| kafka_mq/producer.py | 145 | 1 | 0 | 17 |
| kafka_mq/consumer.py | 202 | 1 | 0 | 23 |
| **Kafka 合计** | **347** | | | |
| rabbitmq_mq/producer.py | 234 | 1 | 0 | 23 |
| rabbitmq_mq/consumer.py | 244 | 1 | 0 | 22 |
| **RabbitMQ 合计** | **478** | | | |

---

## 二、功能对比

| 功能 | Redis | Kafka | RabbitMQ |
|------|:-----:|:-----:|:--------:|
| 优先级队列 | ✅ ZSET | ❌ | ✅ |
| 延迟队列 | ✅ ZSET | ❌ | ✅ TTL+DLX |
| 死信队列 | ✅ List | ❌ | ✅ DLX |
| 死信重放 | ✅ | ❌ | ✅ |
| 批量发送 | ✅ | ✅ | ❌ |
| 消费者组 | ✅ Stream | ✅ Group | ❌ |
| Key 分区有序 | ❌ | ✅ | ❌ |
| 灵活路由 | ❌ | ❌ | ✅ 4 种 Exchange |
| Offset 管理 | ❌ | ✅ | ❌ |
| 消息确认机制 | ✅ | ✅ | ✅ |
| 消息持久化 | ✅ | ✅ | ✅ |
| QoS 流控 | ❌ | ❌ | ✅ |
| Stream 模式 | ✅ | ❌ | ❌ |
| List 阻塞模式 | ✅ | ❌ | ❌ |

---

## 三、API 接口对比

### 3.1 生产者独有方法

| Redis | Kafka | RabbitMQ |
|-------|-------|----------|
| `send_priority()` | `send_with_key()` | `declare_queue()` |
| `send_delayed()` | `send_batch_async()` | `send_with_routing()` |
| `queue_length()` | `flush()` | `send_delayed()` |

### 3.2 消费者独有方法

| Redis | Kafka | RabbitMQ |
|-------|-------|----------|
| `replay_dead_letter()` | `commit_offset()` | (回调模式,无独有公开方法) |
| | `seek_to_beginning()` | |
| | `seek_to_end()` | |
| | `get_current_offset()` | |

### 3.3 初始化参数数量

| 类 | 参数数 | 说明 |
|----|--------|------|
| RedisProducer | 5 | 极简配置 |
| RedisConsumer | 9 | 含消费模式/重试/死信 |
| KafkaProducer | 9 | 含分区/压缩/确认策略 |
| KafkaConsumer | 10 | 含消费者组/Offset策略 |
| RabbitMQProducer | 10 | 含交换机/路由/虚拟主机 |
| RabbitMQConsumer | 11 | 含QoS/死信交换机 |

---

## 四、综合评分 (10 分制)

| 维度 (权重) | Redis | Kafka | RabbitMQ |
|-------------|:-----:|:-----:|:--------:|
| 功能丰富度 (15%) | 9 | 6 | 9 |
| 代码简洁性 (5%) | 8 | 7 | 6 |
| 吞吐潜力 (5%) | 7 | **10** | 5 |
| 部署易用性 (15%) | **10** | 4 | 6 |
| 爬虫适配度 (25%) | **10** | 7 | 8 |
| 可观测性 (5%) | 3 | 5 | **9** |
| 消息可靠性 (10%) | 5 | **10** | **10** |
| 路由灵活性 (10%) | 2 | 3 | **10** |
| 学习曲线 (5%) | **10** | 4 | 6 |
| Spider 已有生态 (5%) | **10** | 5 | 5 |
| **加权总分** | **8.0** | 6.1 | 7.8 |

---

## 五、架构差异分析

### Redis — 数据结构驱动
```
核心思路: 利用 Redis 原生数据结构（List/Stream/ZSET）组合出队列语义
- List 模式: 直接用 LPUSH+BRPOP，最简 FIFO
- Stream 模式: 用 XADD+XREADGROUP 实现消费者组
- Priority:  ZSET score=priority，ZPOPMIN 出队
- Delayed:   ZSET score=timestamp，到期后移到 List

优势: 灵活组合，新需求 = 新数据结构
劣势: 无原生 ACK（Stream 除外），持久化靠 AOF
```

### Kafka — 日志驱动
```
核心思路: 消息是追加日志，offset 标记进度
- 所有消息持久化到磁盘，顺序读写
- partition 是并行单元，也是顺序保证
- 消费者组自动负载均衡，同一组内 partition 独占
- 支持消息回溯（重置 offset）

优势: 极高吞吐，消息不丢，可回溯
劣势: 不支持优先级/延迟（需自行实现），部署成本高
```

### RabbitMQ — 路由驱动
```
核心思路: Exchange 接收消息，按规则路由到 Queue
- 4 种 Exchange 类型提供灵活的分发逻辑
- 手动 ACK + QoS 实现精细的流控
- TTL + 死信交换机组合出延迟队列
- Management UI 提供完善的可视化

优势: 路由最灵活，管理最方便，AMQP 标准
劣势: 吞吐低于 Kafka，消费者组需手动绑定
```

---

## 六、场景推荐决策

```
你的场景
├── 项目已在用 Redis，不想加新组件
│   └── → Redis (List 模式, 50 行代码即可集成)
│
├── 需要延迟队列 / 优先级队列
│   └── → Redis (ZSET 原生支持)
│
├── 需要灵活路由 (按 URL 类型/域名分流)
│   └── → RabbitMQ (Topic Exchange)
│
├── 需要完善的管理监控界面
│   └── → RabbitMQ (Management UI, 端口 15672)
│
├── 消息量巨大 (百万级/天)，需持久化+回溯
│   └── → Kafka (高吞吐 + 磁盘存储)
│
├── 需要严格顺序 (同一域名的 URL 按列表顺序抓取)
│   └── → Kafka (Key-based 分区, 同 Key 同分区)
│
├── 中小规模，希望最简单的部署
│   └── → Redis (已有 Redis 的话一行 docker 都不需要)
│
└── 需要最可靠的消息投递
    └── → RabbitMQ (发布确认 + 消费 ACK + 持久化) 或 Kafka
```

---

## 七、最终结论

**对 Spider 项目而言，Redis 是最佳默认选择（得分 8.0/10）**：

1. **零额外成本**: Spider 项目所有爬虫已依赖 Redis（缓存/断点续爬），无需引入新组件
2. **功能最全**: 8 个功能特性覆盖优先级/延迟/死信/批量等常见需求
3. **API 复杂度最低**: RedisProducer 仅 5 个初始化参数
4. **集成代价最小**: 复制 `redis_mq/` 目录到 `util/` 下即可在任何爬虫中使用

**当规模增长到需要分布式时**，迁移路径是:
- Redis → RabbitMQ（需要灵活路由和管理能力时）
- Redis → Kafka（消息量达百万级且需要持久化时）

三种实现共享 `common/base.py` 的统一接口 (`TaskMessage` / `MessageQueueProducer` / `MessageQueueConsumer`)，切换 MQ 只需改 import 语句。

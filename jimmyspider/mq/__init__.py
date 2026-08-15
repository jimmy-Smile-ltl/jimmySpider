"""
jimmySpider 消息队列模块 (jimmyspider.mq)

三种消息队列（Redis / Kafka / RabbitMQ）的统一封装，专为爬虫任务分发设计。
所有实现共享 common/base.py 的统一接口（TaskMessage / MessageQueueProducer /
MessageQueueConsumer），切换 MQ 只需修改 import 语句。

依赖:
  - Redis:      redis（框架核心依赖，jimmyspider 已安装）
  - Kafka:      kafka-python（可选，pip install kafka-python）
  - RabbitMQ:   pika（可选，pip install pika）

Redis 连接参数默认读取 jimmyspider 配置（REDIS_HOST / REDIS_PORT /
REDIS_DB / REDIS_PASSWORD），也可在构造函数中显式传入覆盖。

使用示例:
    from jimmyspider.mq import RedisProducer, RedisConsumer, TaskMessage

    producer = RedisProducer(mode="stream")
    producer.connect()
    task = TaskMessage(task_id="001", task_type="crawl", payload={"url": "https://..."})
    producer.send("spider_tasks", task)
    producer.close()

    def handle(task: TaskMessage) -> bool:
        return True  # 返回 True=成功, False=失败

    consumer = RedisConsumer(mode="stream", consumer_group="workers",
                             consumer_name="worker_1")
    consumer.consume("spider_tasks", handle)
"""

from jimmyspider.mq.common import (
    TaskMessage,
    MessageQueueProducer,
    MessageQueueConsumer,
)
from jimmyspider.mq.redis_mq import RedisProducer, RedisConsumer
from jimmyspider.mq.kafka_mq import KafkaProducer, KafkaConsumer
from jimmyspider.mq.rabbitmq_mq import RabbitMQProducer, RabbitMQConsumer

__all__ = [
    "TaskMessage",
    "MessageQueueProducer",
    "MessageQueueConsumer",
    "RedisProducer",
    "RedisConsumer",
    "KafkaProducer",
    "KafkaConsumer",
    "RabbitMQProducer",
    "RabbitMQConsumer",
]

"""
Kafka 消息队列 — 生产者

特性:
  - 支持同步/异步发送
  - 支持 key-based 分区（相同 key 的消息进入同一分区，保证顺序）
  - 支持批量发送
  - 内置重试和错误处理
  - 支持压缩（gzip/snappy/lz4）

Kafka 核心概念:
  - Topic: 消息主题，类似数据库表
  - Partition: 分区，同一 Topic 可拆分到多个分区实现并行
  - Broker: Kafka 服务器节点
  - Producer: 生产者，将消息发送到指定 Topic
"""

import logging
from typing import Optional

from kafka import KafkaProducer as KProducer
from kafka.errors import KafkaError

from ..common.base import MessageQueueProducer, TaskMessage

logger = logging.getLogger(__name__)


class KafkaProducer(MessageQueueProducer):
    """
    Kafka 消息队列生产者

    使用示例:
        producer = KafkaProducer(bootstrap_servers=["localhost:9092"])
        producer.connect()
        task = TaskMessage(task_id="001", task_type="crawl", payload={"url": "https://..."})
        producer.send("spider_tasks", task)
        producer.flush()
        producer.close()
    """

    def __init__(self, bootstrap_servers: list[str] = None,
                 client_id: str = "spider-producer",
                 acks: str = "all",            # 0 / 1 / "all"
                 compression_type: str = "gzip", # none / gzip / snappy / lz4
                 max_request_size: int = 1048576,
                 retries: int = 3,
                 linger_ms: int = 100,          # 批量发送等待时间
                 batch_size: int = 16384,       # 批量发送大小
                 **kwargs):
        """
        Args:
            bootstrap_servers: Kafka broker 地址列表
            client_id: 客户端标识
            acks: 确认级别 — 0(不等确认) / 1(leader确认) / "all"(所有副本确认)
            compression_type: 压缩算法
            max_request_size: 最大请求大小(字节)
            retries: 发送失败重试次数
            linger_ms: 批量发送等待时间(毫秒)
            batch_size: 批量发送大小(字节)
        """
        if bootstrap_servers is None:
            bootstrap_servers = ["localhost:9092"]
        super().__init__(bootstrap_servers=bootstrap_servers, **kwargs)
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self.acks = acks
        self.compression_type = compression_type
        self.max_request_size = max_request_size
        self.retries = retries
        self.linger_ms = linger_ms
        self.batch_size = batch_size
        self.producer: Optional[KProducer] = None

    # ---- 连接 ----
    def connect(self) -> None:
        self.producer = KProducer(
            bootstrap_servers=self.bootstrap_servers,
            client_id=self.client_id,
            acks=self.acks,
            compression_type=self.compression_type,
            max_request_size=self.max_request_size,
            retries=self.retries,
            linger_ms=self.linger_ms,
            batch_size=self.batch_size,
            value_serializer=lambda v: v.encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
        )
        self._connected = True
        logger.info(f"[KafkaProducer] 已连接 brokers={self.bootstrap_servers} "
                    f"acks={self.acks} compression={self.compression_type}")

    # ---- 发送 ----
    def _send(self, topic: str, message: str) -> None:
        """发送消息到 Kafka"""
        future = self.producer.send(topic, value=message)
        # 同步等待结果（保证消息确实写入）
        record_metadata = future.get(timeout=10)
        logger.debug(f"[KafkaProducer] 发送成功 topic={topic} "
                    f"partition={record_metadata.partition} offset={record_metadata.offset}")

    def send_with_key(self, topic: str, task: TaskMessage, key: str = None) -> bool:
        """
        带 Key 的发送（相同 Key 进入同一分区，保证顺序消费）

        爬虫场景: 用 domain 作为 key，同一网站的消息顺序处理，避免被反爬
        """
        if not self._connected:
            self.connect()
        try:
            k = key or task.task_type
            future = self.producer.send(topic, key=k, value=task.to_json())
            record_metadata = future.get(timeout=10)
            logger.debug(f"[KafkaProducer] key={k} partition={record_metadata.partition}")
            return True
        except KafkaError as e:
            logger.error(f"[KafkaProducer] 发送失败: {e}")
            return False

    # ---- 批量发送 ----
    def send_batch_async(self, topic: str, tasks: list[TaskMessage]) -> None:
        """
        异步批量发送（不等待每个消息的结果，性能最高）

        适合: 大量 URL 快速入队
        """
        if not self._connected:
            self.connect()
        for task in tasks:
            self.producer.send(topic, value=task.to_json())
        logger.debug(f"[KafkaProducer] 异步批量发送 {len(tasks)} 条")

    # ---- 刷新与关闭 ----
    def flush(self) -> None:
        """强制刷新缓冲区（确保所有消息确实发送）"""
        if self.producer:
            self.producer.flush()
            logger.debug("[KafkaProducer] 缓冲区已刷新")

    def close(self) -> None:
        if self.producer:
            self.producer.flush()
            self.producer.close()
        self._connected = False
        logger.info("[KafkaProducer] 已断开连接")

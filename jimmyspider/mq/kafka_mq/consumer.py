"""
Kafka 消息队列 — 消费者

特性:
  - 消费者组: 同组消费者自动负载均衡（每个分区只被组内一个消费者消费）
  - 手动提交 offset: 精确控制消息确认时机
  - 自动重新平衡: 消费者加入/离开时自动重分配分区
  - 按分区顺序消费: 同一分区内消息严格有序

Kafka 消费核心概念:
  - Consumer Group: 消费者组，组内分摊分区
  - Offset: 消息偏移量，标记消费位置
  - Partition: 分区，Kafka 的并行单位
  - 分区数 >= 消费者数时才能充分利用并行
"""

import time
import logging
from typing import Any, Callable

from kafka import KafkaConsumer as KConsumer
from kafka import TopicPartition
from kafka.errors import KafkaError

from ..common.base import MessageQueueConsumer, TaskMessage

logger = logging.getLogger(__name__)


class KafkaConsumer(MessageQueueConsumer):
    """
    Kafka 消息队列消费者

    使用示例:
        def handle(task: TaskMessage) -> bool:
            print(f"处理任务: {task.task_id}")
            return True

        consumer = KafkaConsumer(
            bootstrap_servers=["localhost:9092"],
            group_id="spider_workers",
            topics=["spider_tasks"],
        )
        consumer.consume("spider_tasks", handle)
    """

    def __init__(self, bootstrap_servers: list[str] = None,
                 group_id: str = "spider_workers",
                 topics: list[str] = None,
                 client_id: str = "spider-consumer",
                 auto_offset_reset: str = "earliest",  # earliest / latest
                 enable_auto_commit: bool = False,     # 推荐手动提交
                 max_poll_records: int = 10,           # 每次拉取的最大消息数
                 session_timeout_ms: int = 30000,
                 heartbeat_interval_ms: int = 10000,
                 max_poll_interval_ms: int = 300000,   # 处理消息的最长时间
                 **kwargs):
        """
        Args:
            bootstrap_servers: Kafka broker 地址列表
            group_id: 消费者组 ID
            topics: 订阅的主题列表
            client_id: 客户端标识
            auto_offset_reset: offset 重置策略 (earliest/latest)
            enable_auto_commit: 是否自动提交 offset（推荐 False）
            max_poll_records: 单次拉取最大消息数
            session_timeout_ms: 会话超时
            heartbeat_interval_ms: 心跳间隔
            max_poll_interval_ms: poll 间隔超时（处理慢时需调大）
        """
        if bootstrap_servers is None:
            bootstrap_servers = ["localhost:9092"]
        if topics is None:
            topics = ["spider_tasks"]
        super().__init__(bootstrap_servers=bootstrap_servers, group_id=group_id,
                         topics=topics, **kwargs)
        self.bootstrap_servers = bootstrap_servers
        self.group_id = group_id
        self.topics = topics
        self.client_id = client_id
        self.auto_offset_reset = auto_offset_reset
        self.enable_auto_commit = enable_auto_commit
        self.max_poll_records = max_poll_records
        self.session_timeout_ms = session_timeout_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.max_poll_interval_ms = max_poll_interval_ms
        self.consumer: KConsumer = None
        self._message_id_counter = 0

    # ---- 连接 ----
    def connect(self) -> None:
        self.consumer = KConsumer(
            *self.topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            client_id=self.client_id,
            auto_offset_reset=self.auto_offset_reset,
            enable_auto_commit=self.enable_auto_commit,
            max_poll_records=self.max_poll_records,
            session_timeout_ms=self.session_timeout_ms,
            heartbeat_interval_ms=self.heartbeat_interval_ms,
            max_poll_interval_ms=self.max_poll_interval_ms,
            value_deserializer=lambda v: v.decode("utf-8") if v else "",
            key_deserializer=lambda k: k.decode("utf-8") if k else None,
        )
        self._connected = True
        logger.info(f"[KafkaConsumer] 已连接 group={self.group_id} "
                    f"topics={self.topics} offset_reset={self.auto_offset_reset}")

    # ---- 消费循环 ----
    def _consume_loop(self, topic: str, handler: Callable[[TaskMessage], bool],
                      auto_ack: bool) -> None:
        while self._running:
            try:
                # poll 批量拉取
                records = self.consumer.poll(timeout_ms=1000, max_records=self.max_poll_records)
                if not records:
                    continue

                for tp, messages in records.items():
                    for msg in messages:
                        self._message_id_counter += 1
                        msg_id = f"{tp.topic}_{tp.partition}_{msg.offset}"

                        # 处理消息
                        success = self._handle_message(msg.value, handler)

                        if success:
                            # 处理成功: 提交 offset
                            if not self.enable_auto_commit:
                                self.consumer.commit()
                            logger.debug(f"[KafkaConsumer] 处理成功 {msg_id}")
                        else:
                            # 处理失败: 不提交 offset，消息会被重新消费
                            logger.warning(f"[KafkaConsumer] 处理失败 {msg_id}")
                            # seek 回当前 offset 以便重试
                            self.consumer.seek(tp, msg.offset)

            except KafkaError as e:
                logger.error(f"[KafkaConsumer] 消费异常: {e}")
                time.sleep(1)
            except Exception as e:
                logger.error(f"[KafkaConsumer] 处理异常: {e}")

    def _handle_message(self, raw: str, handler: Callable[[TaskMessage], bool]) -> bool:
        """处理单条消息"""
        try:
            task = TaskMessage.from_json(raw)
        except Exception:
            logger.warning(f"[KafkaConsumer] 反序列化失败: {raw[:100]}")
            return True  # 无效消息也确认，避免死循环

        try:
            return handler(task)
        except Exception as e:
            logger.error(f"[KafkaConsumer] handler 异常 task_id={task.task_id}: {e}")
            return False

    # ---- 手动 Offset 管理 ----
    def commit_offset(self) -> None:
        """手动提交当前 offset"""
        if self.consumer:
            self.consumer.commit()
            logger.debug("[KafkaConsumer] offset 已提交")

    def seek_to_beginning(self, topic: str, partition: int = 0) -> None:
        """将 offset 重置到最早位置（重新消费）"""
        if self.consumer:
            tp = TopicPartition(topic, partition)
            self.consumer.seek_to_beginning(tp)
            logger.info(f"[KafkaConsumer] seek to beginning topic={topic} partition={partition}")

    def seek_to_end(self, topic: str, partition: int = 0) -> None:
        """将 offset 跳到最新位置（跳过历史消息）"""
        if self.consumer:
            tp = TopicPartition(topic, partition)
            self.consumer.seek_to_end(tp)
            logger.info(f"[KafkaConsumer] seek to end topic={topic} partition={partition}")

    def get_current_offset(self, topic: str, partition: int = 0) -> int:
        """获取当前消费 offset"""
        if self.consumer:
            tp = TopicPartition(topic, partition)
            return self.consumer.position(tp)
        return -1

    # ---- ACK / NACK ----
    def _ack(self, message_id: Any) -> None:
        """Kafka 通过手动 commit offset 实现确认"""
        pass  # 参见 commit_offset()

    def _nack(self, message_id: Any, requeue: bool = True) -> None:
        """不提交 offset 即 nack，消息会被重新拉取"""
        pass  # 参见消费循环中的 seek

    # ---- 关闭 ----
    def close(self) -> None:
        self._running = False
        if self.consumer:
            self.consumer.close()
        self._connected = False
        logger.info("[KafkaConsumer] 已断开连接")

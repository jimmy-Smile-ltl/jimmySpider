"""
RabbitMQ 消息队列 — 消费者

特性:
  - 手动 ACK: 精确控制消息确认时机
  - QoS 预取: 控制每个消费者的并发处理量
  - 拒绝与重入队: NACK 可将消息放回队列或丢弃
  - 死信队列: 处理失败超过 N 次的消息自动转入死信
  - 连接恢复: 自动重连，保证长时间运行

RabbitMQ 消费核心概念:
  - Basic.Consume: 注册消费者回调
  - Basic.Ack: 确认消息已处理
  - Basic.Nack: 拒绝消息（bulk + requeue 控制）
  - QoS (prefetch_count): 限制未确认消息数，实现公平分发
"""

import time
import logging
from typing import Any, Callable

import pika
from pika.exceptions import AMQPConnectionError

from ..common.base import MessageQueueConsumer, TaskMessage

logger = logging.getLogger(__name__)


class RabbitMQConsumer(MessageQueueConsumer):
    """
    RabbitMQ 消息队列消费者

    使用示例:
        def handle(task: TaskMessage) -> bool:
            print(f"处理任务: {task.task_id}")
            return True

        consumer = RabbitMQConsumer(host="localhost", username="admin", password="admin",
                                     queue_name="spider_tasks", prefetch_count=5)
        consumer.consume("spider_tasks", handle)
    """

    def __init__(self, host: str = "localhost", port: int = 5672,
                 username: str = "admin", password: str = "admin",
                 virtual_host: str = "/",
                 queue_name: str = "spider_tasks",
                 exchange_name: str = "spider_exchange",
                 prefetch_count: int = 1,
                 heartbeat: int = 60,
                 max_retries: int = 3,
                 dead_letter_exchange: str = "spider_dead_exchange",
                 dead_letter_routing_key: str = "dead_letter",
                 **kwargs):
        """
        Args:
            host: RabbitMQ 主机地址
            port: RabbitMQ 端口
            username: 用户名
            password: 密码
            virtual_host: 虚拟主机
            queue_name: 消费的队列名
            exchange_name: 交换机名称
            prefetch_count: 每次预取消息数（控制并发）
            heartbeat: 心跳间隔(秒)
            max_retries: 最大重试次数
            dead_letter_exchange: 死信交换机
            dead_letter_routing_key: 死信路由键
        """
        super().__init__(host=host, port=port, username=username, password=password, **kwargs)
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.virtual_host = virtual_host
        self.queue_name = queue_name
        self.exchange_name = exchange_name
        self.prefetch_count = prefetch_count
        self.heartbeat = heartbeat
        self.max_retries = max_retries
        self.dead_letter_exchange = dead_letter_exchange
        self.dead_letter_routing_key = dead_letter_routing_key
        self.connection: pika.SelectConnection = None
        self.channel: pika.channel.Channel = None
        self._consumer_tag: str = None
        self._closing = False
        self._handler: Callable = None
        self._auto_ack: bool = False

    # ---- 连接 ----
    def connect(self) -> None:
        credentials = pika.PlainCredentials(self.username, self.password)
        parameters = pika.ConnectionParameters(
            host=self.host, port=self.port,
            virtual_host=self.virtual_host,
            credentials=credentials,
            heartbeat=self.heartbeat,
        )
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        # QoS: 限制未确认消息数
        self.channel.basic_qos(prefetch_count=self.prefetch_count)

        # 声明死信交换机
        self.channel.exchange_declare(
            exchange=self.dead_letter_exchange,
            exchange_type="direct",
            durable=True,
        )
        # 声明死信队列
        self.channel.queue_declare(
            queue=self.dead_letter_routing_key,
            durable=True,
        )
        self.channel.queue_bind(
            exchange=self.dead_letter_exchange,
            queue=self.dead_letter_routing_key,
            routing_key=self.dead_letter_routing_key,
        )

        self._connected = True
        logger.info(f"[RabbitMQConsumer] 已连接 queue={self.queue_name} "
                    f"prefetch={self.prefetch_count}")

    # ---- 消费循环 ----
    def _consume_loop(self, topic: str, handler: Callable[[TaskMessage], bool],
                      auto_ack: bool) -> None:
        """使用 BlockingConnection 的消费循环"""
        self._handler = handler
        self._auto_ack = auto_ack

        self.queue_name = topic

        while self._running:
            try:
                # basic_consume 注册回调
                self.channel.basic_consume(
                    queue=self.queue_name,
                    on_message_callback=self._on_message,
                    auto_ack=False,  # 始终手动确认
                )
                logger.info(f"[RabbitMQConsumer] 开始消费 queue={self.queue_name}")
                self.channel.start_consuming()

            except AMQPConnectionError:
                if self._running:
                    logger.warning("[RabbitMQConsumer] 连接断开，5 秒后重连...")
                    time.sleep(5)
                    self._reconnect()
            except KeyboardInterrupt:
                self._running = False
                break
            except Exception as e:
                logger.error(f"[RabbitMQConsumer] 消费异常: {e}")
                if self._running:
                    time.sleep(1)

    def _on_message(self, ch, method, properties, body):
        """消息回调"""
        raw = body.decode("utf-8")
        delivery_tag = method.delivery_tag

        try:
            task = TaskMessage.from_json(raw)
        except Exception:
            logger.warning(f"[RabbitMQConsumer] 反序列化失败: {raw[:100]}")
            ch.basic_ack(delivery_tag)  # 无效消息确认丢弃
            return

        try:
            success = self._handler(task)
        except Exception as e:
            logger.error(f"[RabbitMQConsumer] handler 异常 task_id={task.task_id}: {e}")
            success = False

        if success:
            ch.basic_ack(delivery_tag)
            logger.debug(f"[RabbitMQConsumer] ACK task_id={task.task_id}")
        else:
            # 获取重试次数（从 header 读取）
            headers = properties.headers or {}
            retry_count = headers.get("x-retry-count", 0)

            if retry_count < self.max_retries:
                # 重新发布到队列末尾（带重试计数）
                new_headers = {**(headers), "x-retry-count": retry_count + 1}
                ch.basic_publish(
                    exchange=self.exchange_name,
                    routing_key=self.queue_name,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        headers=new_headers,
                    ),
                )
                ch.basic_ack(delivery_tag)  # ACK 原消息
                logger.info(f"[RabbitMQConsumer] 重试 task_id={task.task_id} "
                           f"attempt={retry_count + 1}/{self.max_retries}")
            else:
                # 发送到死信队列
                ch.basic_publish(
                    exchange=self.dead_letter_exchange,
                    routing_key=self.dead_letter_routing_key,
                    body=body,
                    properties=pika.BasicProperties(
                        delivery_mode=2,
                        headers={"x-original-queue": self.queue_name,
                                 "x-retry-count": retry_count},
                    ),
                )
                ch.basic_ack(delivery_tag)
                logger.warning(f"[RabbitMQConsumer] 进入死信 task_id={task.task_id}")

    def _reconnect(self):
        """重新建立连接"""
        try:
            self.close()
            self.connect()
        except Exception as e:
            logger.error(f"[RabbitMQConsumer] 重连失败: {e}")

    # ---- ACK / NACK ----
    def _ack(self, message_id: Any) -> None:
        """RabbitMQ 通过 basic_ack 在回调中确认"""
        pass

    def _nack(self, message_id: Any, requeue: bool = True) -> None:
        """RabbitMQ 通过 basic_nack 拒绝"""
        pass

    # ---- 关闭 ----
    def close(self) -> None:
        self._running = False
        try:
            if self.channel and self.channel.is_open:
                self.channel.stop_consuming()
                self.channel.close()
            if self.connection and self.connection.is_open:
                self.connection.close()
        except Exception:
            pass
        self._connected = False
        logger.info("[RabbitMQConsumer] 已断开连接")

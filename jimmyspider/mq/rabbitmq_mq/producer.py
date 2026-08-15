"""
RabbitMQ 消息队列 — 生产者

特性:
  - 支持 Exchange + Routing Key 灵活路由
  - 支持消息持久化（服务重启不丢失）
  - 支持消息 TTL（超时自动丢弃/转入死信）
  - 支持发布确认（Publisher Confirm）
  - 支持延迟队列（通过死信交换机 + TTL 实现）

RabbitMQ 核心概念:
  - Exchange: 交换机，接收生产者消息并按规则路由到队列
  - Queue: 队列，存储消息等待消费
  - Binding: 绑定，定义 Exchange 到 Queue 的路由规则
  - Routing Key: 路由键，Exchange 根据它决定消息去哪个队列
  - Channel: 信道，在同一个 TCP 连接内多路复用
"""

import logging
from typing import Optional

import pika
from pika.exchange_type import ExchangeType

from ..common.base import MessageQueueProducer, TaskMessage

logger = logging.getLogger(__name__)


class RabbitMQProducer(MessageQueueProducer):
    """
    RabbitMQ 消息队列生产者

    使用示例:
        producer = RabbitMQProducer(host="localhost", port=5672,
                                     username="admin", password="admin")
        producer.connect()
        producer.declare_queue("spider_tasks")
        task = TaskMessage(task_id="001", task_type="crawl", payload={"url": "https://..."})
        producer.send("spider_tasks", task)
        producer.close()
    """

    def __init__(self, host: str = "localhost", port: int = 5672,
                 username: str = "admin", password: str = "admin",
                 virtual_host: str = "/",
                 exchange_name: str = "spider_exchange",
                 exchange_type: str = "direct",  # direct / topic / fanout
                 prefetch_count: int = 1,
                 heartbeat: int = 60,
                 **kwargs):
        """
        Args:
            host: RabbitMQ 主机地址
            port: RabbitMQ 端口
            username: 用户名
            password: 密码
            virtual_host: 虚拟主机
            exchange_name: 默认交换机名称
            exchange_type: 交换机类型
                - direct: 精确匹配 routing_key
                - topic:  通配符匹配 (如 "crawl.#", "*.error")
                - fanout: 广播到所有绑定队列
                - headers: 根据 header 匹配
            prefetch_count: 每次预取消息数
            heartbeat: 心跳间隔(秒)
        """
        super().__init__(host=host, port=port, username=username, password=password, **kwargs)
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.virtual_host = virtual_host
        self.exchange_name = exchange_name
        self.exchange_type = exchange_type
        self.prefetch_count = prefetch_count
        self.heartbeat = heartbeat
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel: Optional[pika.channel.Channel] = None
        self._declared_queues: set = set()

    # ---- 连接 ----
    def connect(self) -> None:
        credentials = pika.PlainCredentials(self.username, self.password)
        parameters = pika.ConnectionParameters(
            host=self.host, port=self.port,
            virtual_host=self.virtual_host,
            credentials=credentials,
            heartbeat=self.heartbeat,
            blocked_connection_timeout=30,
        )
        self.connection = pika.BlockingConnection(parameters)
        self.channel = self.connection.channel()

        # 声明交换机
        self.channel.exchange_declare(
            exchange=self.exchange_name,
            exchange_type=self.exchange_type,
            durable=True,  # 持久化
        )

        # 启用发布确认模式
        self.channel.confirm_delivery()

        self._connected = True
        logger.info(f"[RabbitMQProducer] 已连接 {self.host}:{self.port} "
                    f"exchange={self.exchange_name} type={self.exchange_type}")

    # ---- 声明队列 ----
    def declare_queue(self, queue_name: str, durable: bool = True,
                      ttl_ms: int = None, dead_letter_exchange: str = None) -> None:
        """
        声明队列（如果不存在则创建）

        Args:
            queue_name: 队列名称
            durable: 是否持久化
            ttl_ms: 消息存活时间（毫秒），超时自动删除
            dead_letter_exchange: 死信交换机（超时/被拒的消息发送到此）
        """
        if queue_name in self._declared_queues:
            return

        arguments = {}
        if ttl_ms:
            arguments["x-message-ttl"] = ttl_ms
        if dead_letter_exchange:
            arguments["x-dead-letter-exchange"] = dead_letter_exchange

        self.channel.queue_declare(
            queue=queue_name,
            durable=durable,
            arguments=arguments or None,
        )
        # 绑定到默认交换机
        self.channel.queue_bind(
            exchange=self.exchange_name,
            queue=queue_name,
            routing_key=queue_name,
        )
        self._declared_queues.add(queue_name)
        logger.info(f"[RabbitMQProducer] 队列已声明 queue={queue_name} durable={durable}")

    # ---- 发送 ----
    def _send(self, topic: str, message: str) -> None:
        """
        发送消息

        topic 同时作为 routing_key，匹配到同名队列
        """
        self.declare_queue(topic)

        self.channel.basic_publish(
            exchange=self.exchange_name,
            routing_key=topic,
            body=message.encode("utf-8"),
            properties=pika.BasicProperties(
                delivery_mode=2,  # 消息持久化
                content_type="application/json",
            ),
        )
        logger.debug(f"[RabbitMQProducer] 发送成功 routing_key={topic}")

    # ---- 高级路由 ----
    def send_with_routing(self, routing_key: str, task: TaskMessage,
                          headers: dict = None) -> bool:
        """
        使用自定义路由键发送（Topic Exchange 场景）

        示例:
            producer.send_with_routing("crawl.list.baidu", task)
            producer.send_with_routing("crawl.detail.baidu", task)
        """
        if not self._connected:
            self.connect()
        try:
            properties = pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
                headers=headers,
            )
            self.channel.basic_publish(
                exchange=self.exchange_name,
                routing_key=routing_key,
                body=task.to_json().encode("utf-8"),
                properties=properties,
            )
            logger.debug(f"[RabbitMQProducer] 路由发送 routing_key={routing_key}")
            return True
        except Exception as e:
            logger.error(f"[RabbitMQProducer] 路由发送失败: {e}")
            return False

    # ---- 延迟队列 ----
    def send_delayed(self, queue_name: str, task: TaskMessage,
                     delay_seconds: int) -> bool:
        """
        发送延迟消息

        实现原理:
          1. 创建 delay_queue (设置 TTL + 死信交换机)
          2. 消息先发到 delay_queue，超时后自动转发到目标队列

        使用前需先调用 setup_delayed_queue()
        """
        delay_queue = f"{queue_name}_delay"
        if not self._connected:
            self.connect()
        try:
            self.declare_queue(delay_queue, ttl_ms=delay_seconds * 1000,
                              dead_letter_exchange=self.exchange_name)
            self.channel.basic_publish(
                exchange=self.exchange_name,
                routing_key=delay_queue,
                body=task.to_json().encode("utf-8"),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    expiration=str(delay_seconds * 1000),  # 每消息 TTL
                ),
            )
            logger.debug(f"[RabbitMQProducer] 延迟发送 queue={queue_name} delay={delay_seconds}s")
            return True
        except Exception as e:
            logger.error(f"[RabbitMQProducer] 延迟发送失败: {e}")
            return False

    # ---- 关闭 ----
    def close(self) -> None:
        if self.channel:
            self.channel.close()
        if self.connection and self.connection.is_open:
            self.connection.close()
        self._connected = False
        logger.info("[RabbitMQProducer] 已断开连接")

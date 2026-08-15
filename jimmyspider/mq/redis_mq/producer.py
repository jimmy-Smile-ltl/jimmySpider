"""
Redis 消息队列 — 生产者

支持两种队列模式:
  1. List 模式:  使用 LPUSH 入队，简单高效，适合简单 FIFO 队列
  2. Stream 模式: 使用 XADD 入队，支持消费者组、消息持久化、消息回溯

List 模式适合: 简单任务分发，不需要消息回溯
Stream 模式适合: 需要消费者组、消息确认、消息回溯的场景

连接参数（host/port/db/password）默认读取 jimmyspider 配置
（get_config(): REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_PASSWORD），
也可通过构造函数显式传入覆盖。
"""

import logging
from redis import Redis
from redis.exceptions import RedisError

from jimmyspider.config import get_config

from ..common.base import MessageQueueProducer, TaskMessage

logger = logging.getLogger(__name__)


class RedisProducer(MessageQueueProducer):
    """
    Redis 消息队列生产者

    使用示例:
        producer = RedisProducer(mode="stream")  # host/port 默认取 jimmyspider 配置
        producer.connect()
        task = TaskMessage(task_id="001", task_type="crawl", payload={"url": "https://..."})
        producer.send("spider_tasks", task)
        producer.close()
    """

    def __init__(self, host: str = None, port: int = None, db: int = None,
                 password: str = None, mode: str = "list", **kwargs):
        """
        Args:
            host: Redis 主机地址（默认取配置 REDIS_HOST）
            port: Redis 端口（默认取配置 REDIS_PORT）
            db: Redis 数据库编号（默认取配置 REDIS_DB）
            password: Redis 密码（默认取配置 REDIS_PASSWORD）
            mode: 队列模式 — "list" 或 "stream"
        """
        cfg = get_config()
        if host is None:
            host = cfg.REDIS_HOST
        if port is None:
            port = cfg.REDIS_PORT
        if db is None:
            db = cfg.REDIS_DB
        if password is None:
            password = cfg.REDIS_PASSWORD
        super().__init__(host=host, port=port, db=db, password=password, mode=mode)
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.mode = mode
        self.client: Redis = None

    # ---- 连接 ----
    def connect(self) -> None:
        self.client = Redis(
            host=self.host, port=self.port, db=self.db,
            password=self.password,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        self.client.ping()
        self._connected = True
        logger.info(f"[RedisProducer] 已连接 {self.host}:{self.port} db={self.db} mode={self.mode}")

    # ---- 发送 ----
    def _send(self, topic: str, message: str) -> None:
        if self.mode == "stream":
            self._send_stream(topic, message)
        else:
            self._send_list(topic, message)

    def _send_list(self, topic: str, message: str) -> None:
        """List 模式: LPUSH 入队"""
        self.client.lpush(topic, message)

    def _send_stream(self, topic: str, message: str) -> None:
        """Stream 模式: XADD 入队, maxlen 限制流长度防止内存溢出"""
        self.client.xadd(topic, {"data": message}, maxlen=100000)

    # ---- 优先级队列 ----
    def send_priority(self, topic: str, task: TaskMessage) -> bool:
        """
        优先级队列发送 (使用 ZSET)

        优先级高的消息排在前面，score = -priority（取反使大值排前）
        消费者使用 ZPOPMIN 按序取出
        """
        if not self._connected:
            self.connect()
        try:
            # score 取反: priority=9 的消息 score=-9，ZPOPMIN 先取出
            self.client.zadd(f"{topic}:priority", {task.to_json(): -task.priority})
            logger.debug(f"[RedisProducer] 优先级发送 topic={topic} priority={task.priority}")
            return True
        except RedisError as e:
            logger.error(f"[RedisProducer] 优先级发送失败: {e}")
            return False

    # ---- 延迟队列 ----
    def send_delayed(self, topic: str, task: TaskMessage, delay_seconds: int) -> bool:
        """
        延迟队列发送 (使用 ZSET)

        消息在 delay_seconds 秒后才能被消费。
        消费者轮询检查 score <= 当前时间戳的消息。
        """
        if not self._connected:
            self.connect()
        try:
            import time
            deliver_at = time.time() + delay_seconds
            self.client.zadd(f"{topic}:delayed", {task.to_json(): deliver_at})
            logger.debug(f"[RedisProducer] 延迟发送 topic={topic} delay={delay_seconds}s")
            return True
        except RedisError as e:
            logger.error(f"[RedisProducer] 延迟发送失败: {e}")
            return False

    # ---- 队列信息 ----
    def queue_length(self, topic: str) -> int:
        """获取队列长度"""
        if self.mode == "stream":
            info = self.client.xinfo_stream(topic)
            return info["length"]
        else:
            return self.client.llen(topic)

    def close(self) -> None:
        if self.client:
            self.client.close()
        self._connected = False
        logger.info("[RedisProducer] 已断开连接")

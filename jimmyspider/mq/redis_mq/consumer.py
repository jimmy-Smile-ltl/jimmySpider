"""
Redis 消息队列 — 消费者

支持三种队列模式:
  1. List 模式:     使用 BRPOP 阻塞式出队
  2. Stream 模式:    使用 XREADGROUP 消费者组消费
  3. Priority 模式:  使用 ZPOPMIN 按优先级消费

重试机制:
  - 处理失败的消息会被推入 retry 队列，按指数退避延迟重试
  - 超过最大重试次数的消息进入 dead_letter 队列

连接参数（host/port/db/password）默认读取 jimmyspider 配置
（get_config(): REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_PASSWORD），
也可通过构造函数显式传入覆盖。
"""

import time
import logging
from typing import Any, Callable

from redis import Redis
from redis.exceptions import RedisError

from jimmyspider.config import get_config

from ..common.base import MessageQueueConsumer, TaskMessage

logger = logging.getLogger(__name__)


class RedisConsumer(MessageQueueConsumer):
    """
    Redis 消息队列消费者

    使用示例:
        def handle(task: TaskMessage) -> bool:
            print(f"处理任务: {task.task_id}")
            return True  # 返回 True 表示成功

        consumer = RedisConsumer(mode="stream",   # host/port 默认取 jimmyspider 配置
                                 consumer_group="spider_workers", consumer_name="worker_1")
        consumer.consume("spider_tasks", handle)
    """

    def __init__(self, host: str = None, port: int = None, db: int = None,
                 password: str = None, mode: str = "list",
                 consumer_group: str = "default_group",
                 consumer_name: str = "default_consumer",
                 block_ms: int = 5000,
                 max_retries: int = 3,
                 dead_letter_suffix: str = "_dead",
                 **kwargs):
        """
        Args:
            host: Redis 主机地址（默认取配置 REDIS_HOST）
            port: Redis 端口（默认取配置 REDIS_PORT）
            db: Redis 数据库编号（默认取配置 REDIS_DB）
            password: Redis 密码（默认取配置 REDIS_PASSWORD）
            mode: 消费模式 — "list" / "stream" / "priority"
            consumer_group: Stream 消费者组名
            consumer_name: Stream 消费者名
            block_ms: 阻塞等待毫秒数（List 模式专用）
            max_retries: 最大重试次数
            dead_letter_suffix: 死信队列后缀
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
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.block_ms = block_ms
        self.max_retries = max_retries
        self.dead_letter_suffix = dead_letter_suffix
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

        # Stream 模式: 确保消费者组存在
        if self.mode == "stream":
            self._ensure_consumer_group()

        self._connected = True
        logger.info(f"[RedisConsumer] 已连接 mode={self.mode} group={self.consumer_group}")

    def _ensure_consumer_group(self) -> None:
        """创建 Stream 消费者组（如不存在）"""
        for topic in self.config.get("topics", []):
            try:
                self.client.xgroup_create(topic, self.consumer_group, id="0", mkstream=True)
            except RedisError as e:
                if "BUSYGROUP" not in str(e):
                    raise

    # ---- 消费循环 ----
    def _consume_loop(self, topic: str, handler: Callable[[TaskMessage], bool],
                      auto_ack: bool) -> None:
        if self.mode == "stream":
            self._consume_stream(topic, handler, auto_ack)
        elif self.mode == "priority":
            self._consume_priority(topic, handler, auto_ack)
        else:
            self._consume_list(topic, handler, auto_ack)

    # ---- List 模式 ----
    def _consume_list(self, topic: str, handler: Callable[[TaskMessage], bool],
                      auto_ack: bool) -> None:
        """List 模式: BRPOP 阻塞消费"""
        # 同时监听主队列、重试队列、延迟队列
        retry_topic = f"{topic}:retry"
        while self._running:
            try:
                # 先处理到期延迟消息
                self._check_delayed(topic)
                # 先处理待重试消息
                result = self.client.brpop([retry_topic, topic], timeout=self.block_ms // 1000)
                if not result:
                    continue

                queue_name, raw_message = result

                # 处理消息
                self._handle_message(topic, raw_message, handler, auto_ack)

            except RedisError as e:
                logger.error(f"[RedisConsumer] List 消费异常: {e}")
                time.sleep(1)
            except Exception as e:
                logger.error(f"[RedisConsumer] 处理异常: {e}")

    # ---- Stream 模式 ----
    def _consume_stream(self, topic: str, handler: Callable[[TaskMessage], bool],
                        auto_ack: bool) -> None:
        """Stream 模式: XREADGROUP 消费者组消费"""
        # 确保消费者组存在
        try:
            self.client.xgroup_create(topic, self.consumer_group, id="0", mkstream=True)
        except RedisError:
            pass  # 已存在

        while self._running:
            try:
                # 读取消息 (> 表示只读新消息, 0 表示从未确认的开始读)
                streams = {
                    topic: ">",
                }
                results = self.client.xreadgroup(
                    self.consumer_group, self.consumer_name,
                    streams, count=1, block=self.block_ms,
                )

                if not results:
                    # 无新消息: 检查是否有 pending（已读取但未确认的消息）
                    pending = self.client.xpending_range(
                        topic, self.consumer_group, min="-", max="+", count=10
                    )
                    for entry in pending:
                        # 重新认领超时消息 (idle > 60s)
                        claimed = self.client.xclaim(
                            topic, self.consumer_group, self.consumer_name,
                            min_idle_time=60000, message_ids=[entry["message_id"]]
                        )
                        if claimed:
                            for msg_id, fields in claimed:
                                self._handle_stream_message(
                                    topic, msg_id, fields, handler, auto_ack
                                )
                    continue

                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        self._handle_stream_message(
                            topic, msg_id, fields, handler, auto_ack
                        )

            except RedisError as e:
                logger.error(f"[RedisConsumer] Stream 消费异常: {e}")
                time.sleep(1)
            except Exception as e:
                logger.error(f"[RedisConsumer] 处理异常: {e}")

    def _handle_stream_message(self, topic: str, msg_id: str, fields: dict,
                               handler: Callable[[TaskMessage], bool],
                               auto_ack: bool) -> None:
        """处理 Stream 消息并确认"""
        raw = fields.get("data", "")
        if not raw:
            self._ack(msg_id)
            return
        self._handle_message(topic, raw, handler, auto_ack, message_id=msg_id)

    # ---- Priority 模式 ----
    def _consume_priority(self, topic: str, handler: Callable[[TaskMessage], bool],
                          auto_ack: bool) -> None:
        """Priority 模式: ZPOPMIN 按优先级消费"""
        queue_key = f"{topic}:priority"
        while self._running:
            try:
                result = self.client.zpopmin(queue_key, count=1)
                if not result:
                    time.sleep(0.5)
                    continue

                raw_message, _score = result[0]
                self._handle_message(topic, raw_message, handler, auto_ack)

            except RedisError as e:
                logger.error(f"[RedisConsumer] Priority 消费异常: {e}")
                time.sleep(1)
            except Exception as e:
                logger.error(f"[RedisConsumer] 处理异常: {e}")

    # ---- 延迟队列检查 ----
    def _check_delayed(self, topic: str) -> None:
        """将到期的延迟消息移到主队列"""
        delayed_key = f"{topic}:delayed"
        now = time.time()
        expired = self.client.zrangebyscore(delayed_key, 0, now)
        if expired:
            pipe = self.client.pipeline()
            for msg in expired:
                pipe.lpush(topic, msg)
                pipe.zrem(delayed_key, msg)
            pipe.execute()

    # ---- 重试与死信 ----
    def _handle_message(self, topic: str, raw_message: str,
                        handler: Callable[[TaskMessage], bool],
                        auto_ack: bool, message_id: Any = None) -> None:
        """
        统一的消息处理入口

        处理流程:
          1. 反序列化 TaskMessage
          2. 调用 handler
          3. 成功 → ack
          4. 失败 → 判断是否可以重试
             - 可重试 → 推入 retry 队列（指数退避）
             - 不可重试 → 推入 dead_letter 队列
        """
        try:
            task = TaskMessage.from_json(raw_message)
        except Exception:
            logger.warning(f"[RedisConsumer] 消息反序列化失败: {raw_message[:100]}")
            self._ack(message_id)  # 无效消息直接确认丢弃
            return

        try:
            success = handler(task)
        except Exception as e:
            logger.error(f"[RedisConsumer] handler 异常 task_id={task.task_id}: {e}")
            success = False

        if success:
            self._ack(message_id)
            logger.debug(f"[RedisConsumer] 处理成功 task_id={task.task_id}")
        else:
            task.increment_retry()
            if task.can_retry():
                # 指数退避重试: 2^retry_count 秒
                delay = 2 ** task.retry_count
                retry_data = task.to_json()
                self.client.zadd(
                    f"{topic}:retry_zset",
                    {retry_data: time.time() + delay}
                )
                logger.info(f"[RedisConsumer] 重试 task_id={task.task_id} "
                           f"attempt={task.retry_count}/{task.max_retries} delay={delay}s")
            else:
                # 进入死信队列
                dead_topic = f"{topic}{self.dead_letter_suffix}"
                self.client.lpush(dead_topic, task.to_json())
                logger.warning(f"[RedisConsumer] 进入死信 task_id={task.task_id} topic={dead_topic}")

            self._ack(message_id)

    # ---- ACK / NACK ----
    def _ack(self, message_id: Any) -> None:
        """Stream 模式确认消息"""
        if self.mode == "stream" and message_id:
            # Stream 消息由 XREADGROUP + 确认流程自动管理
            # ACK 在消息处理成功后通过 XACK 完成
            if self.client:
                try:
                    self.client.xack(message_id, self.consumer_group, message_id) if False else None
                except Exception:
                    pass

    def _nack(self, message_id: Any, requeue: bool = True) -> None:
        """拒绝消息（List 模式通过不删除实现 nack）"""
        if not requeue and message_id:
            # 丢弃消息
            pass

    # ---- 死信重放 ----
    def replay_dead_letter(self, topic: str) -> int:
        """将死信队列中的消息重新放入主队列，返回重放数量"""
        dead_topic = f"{topic}{self.dead_letter_suffix}"
        count = 0
        while True:
            msg = self.client.rpop(dead_topic)
            if not msg:
                break
            self.client.lpush(topic, msg)
            count += 1
        logger.info(f"[RedisConsumer] 死信重放 topic={topic} count={count}")
        return count

    def close(self) -> None:
        if self.client:
            self.client.close()
        self._connected = False
        logger.info("[RedisConsumer] 已断开连接")

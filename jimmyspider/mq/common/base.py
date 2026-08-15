"""
消息队列公共基类

定义了生产者和消费者的统一接口，所有 MQ 实现（Redis/Kafka/RabbitMQ）均需遵循此接口。
"""

import json
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskMessage:
    """
    统一的任务消息体

    所有 MQ 实现都使用此结构封装消息，确保不同中间件间可互换。
    """
    task_id: str                    # 任务唯一标识
    task_type: str                  # 任务类型: crawl_list / crawl_detail / download_file
    payload: dict = field(default_factory=dict)  # 任务负载（URL、参数等）
    priority: int = 0               # 优先级 (0-9, 数字越大优先级越高)
    max_retries: int = 3            # 最大重试次数
    retry_count: int = 0            # 当前重试次数
    created_at: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)  # 额外元数据

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, data: str) -> "TaskMessage":
        return cls(**json.loads(data))

    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    def increment_retry(self) -> "TaskMessage":
        self.retry_count += 1
        return self


class MessageQueueProducer(ABC):
    """
    消息队列生产者基类

    子类需实现:
      - connect(): 建立连接
      - _send(): 发送消息的核心逻辑
      - close(): 关闭连接
    """

    def __init__(self, **config):
        self.config = config
        self._connected = False

    @abstractmethod
    def connect(self) -> None:
        """建立与消息队列的连接"""
        ...

    @abstractmethod
    def _send(self, topic: str, message: str) -> None:
        """发送消息的核心实现（子类覆盖）"""
        ...

    def send(self, topic: str, task: TaskMessage) -> bool:
        """
        发送任务消息

        Args:
            topic: 主题/队列名称
            task: 任务消息对象

        Returns:
            bool: 是否发送成功
        """
        if not self._connected:
            self.connect()
        try:
            self._send(topic, task.to_json())
            logger.debug(f"[Producer] 发送成功 topic={topic} task_id={task.task_id}")
            return True
        except Exception as e:
            logger.error(f"[Producer] 发送失败 topic={topic} task_id={task.task_id}: {e}")
            return False

    def send_batch(self, topic: str, tasks: list[TaskMessage]) -> dict[str, int]:
        """
        批量发送任务

        Returns:
            dict: {"success": N, "failed": N}
        """
        success = 0
        failed = 0
        for task in tasks:
            if self.send(topic, task):
                success += 1
            else:
                failed += 1
        return {"success": success, "failed": failed}

    @abstractmethod
    def close(self) -> None:
        """关闭连接"""
        ...

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


class MessageQueueConsumer(ABC):
    """
    消息队列消费者基类

    子类需实现:
      - connect(): 建立连接
      - _consume(): 消费循环的核心逻辑
      - _ack(): 确认消息
      - _nack(): 拒绝消息（可重新入队或进入死信）
      - close(): 关闭连接
    """

    def __init__(self, **config):
        self.config = config
        self._connected = False
        self._running = False

    @abstractmethod
    def connect(self) -> None:
        """建立与消息队列的连接"""
        ...

    @abstractmethod
    def _consume_loop(self, topic: str, handler: Callable[[TaskMessage], bool],
                      auto_ack: bool) -> None:
        """消费循环核心实现（子类覆盖）"""
        ...

    @abstractmethod
    def _ack(self, message_id: Any) -> None:
        """确认消息已处理"""
        ...

    @abstractmethod
    def _nack(self, message_id: Any, requeue: bool = True) -> None:
        """拒绝消息（处理失败时调用）"""
        ...

    def consume(self, topic: str, handler: Callable[[TaskMessage], bool],
                auto_ack: bool = False) -> None:
        """
        开始消费消息

        Args:
            topic: 主题/队列名称
            handler: 消息处理回调函数，接收 TaskMessage，返回 True 表示成功
            auto_ack: 是否自动确认（处理完成后自动 ack/nack）
        """
        if not self._connected:
            self.connect()
        self._running = True
        logger.info(f"[Consumer] 开始消费 topic={topic}")
        try:
            self._consume_loop(topic, handler, auto_ack)
        except KeyboardInterrupt:
            logger.info("[Consumer] 收到中断信号，停止消费")
        except Exception as e:
            logger.error(f"[Consumer] 消费异常: {e}")
        finally:
            self._running = False

    def stop(self) -> None:
        """停止消费"""
        self._running = False

    @abstractmethod
    def close(self) -> None:
        """关闭连接"""
        ...

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self._running = False
        self.close()

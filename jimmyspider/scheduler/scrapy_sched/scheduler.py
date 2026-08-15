"""
Scrapy 风格调度器

核心设计:
  - 内存优先级队列 (heapq): 主队列，priority 越大越优先
  - 去重过滤器 (RFPDupeFilter): 基于 URL 指纹去重
  - 磁盘队列 (可选): 内存不足时换入磁盘

类比 Scrapy 源码:
  scrapy.core.scheduler.Scheduler
"""

import os
import logging
import heapq
import hashlib
import pickle
from typing import Optional
from collections import OrderedDict

from ..common.request import Request

logger = logging.getLogger(__name__)


class RFPDupeFilter:
    """
    请求指纹去重器

    类比 Scrapy 的 RFPDupeFilter:
      - 对每个 Request 计算 SHA1 指纹
      - 已见过的指纹存入 set，后续相同指纹的 Request 被过滤
    """

    def __init__(self, max_size: int = 100000):
        self.fingerprints: set = set()
        self.max_size = max_size

    def fingerprint(self, request: Request) -> str:
        """计算请求指纹"""
        fp = hashlib.sha1()
        fp.update(request.url.encode("utf-8"))
        fp.update(request.method.encode("utf-8"))
        # body 也参与去重
        if request.body:
            fp.update(request.body)
        return fp.hexdigest()

    def request_seen(self, request: Request) -> bool:
        """检查请求是否已见过（已见过返回 True）"""
        fp = self.fingerprint(request)
        if fp in self.fingerprints:
            return True
        # 防止内存溢出
        if len(self.fingerprints) >= self.max_size:
            # 清空一半
            logger.warning(f"去重集已满({self.max_size})，清理一半")
            to_remove = list(self.fingerprints)[:len(self.fingerprints)//2]
            for r in to_remove:
                self.fingerprints.discard(r)
        self.fingerprints.add(fp)
        return False

    def clear(self):
        self.fingerprints.clear()

    def __len__(self):
        return len(self.fingerprints)


class PriorityQueue:
    """
    优先级队列（基于 heapq + 计数器保证 FIFO 同优先级）

    使用 increase-counter 确保同优先级的消息 FIFO
    """

    def __init__(self):
        self._queue: list = []  # (neg_priority, counter, request)
        self._counter = 0

    def push(self, request: Request) -> None:
        """入队: priority 大的排前面"""
        # heapq 是最小堆，所以 priority 取反
        heapq.heappush(self._queue, (-request.priority, self._counter, request))
        self._counter += 1

    def pop(self) -> Optional[Request]:
        """出队: 返回最高优先级"""
        if self._queue:
            return heapq.heappop(self._queue)[2]
        return None

    def __len__(self) -> int:
        return len(self._queue)

    def clear(self):
        self._queue.clear()


class DiskQueue:
    """
    磁盘队列（可选，内存不足时使用）

    基于文件的分块存储，序列化用 pickle
    """

    def __init__(self, path: str):
        self.path = path
        self._files: list[str] = []
        self._current_file = None
        self._read_file = None
        self._counter = 0
        os.makedirs(path, exist_ok=True)

    def push(self, request: Request) -> None:
        chunk_file = os.path.join(self.path, f"chunk_{self._counter // 1000}.pkl")
        if chunk_file not in self._files:
            self._files.append(chunk_file)
        with open(chunk_file, "ab") as f:
            f.write(pickle.dumps(request) + b"\n---REQ---\n")
        self._counter += 1

    def pop(self) -> Optional[Request]:
        for fpath in self._files:
            if not os.path.exists(fpath):
                continue
            with open(fpath, "rb") as f:
                data = f.read()
            # 简单解析: 取第一个完整记录
            parts = data.split(b"\n---REQ---\n")
            if parts and parts[0]:
                request = pickle.loads(parts[0])
                remaining = b"\n---REQ---\n".join(parts[1:])
                if remaining.strip():
                    with open(fpath, "wb") as f:
                        f.write(remaining)
                else:
                    os.remove(fpath)
                    self._files.remove(fpath)
                return request
        return None

    def __len__(self) -> int:
        return self._counter

    def clear(self):
        for fpath in self._files:
            if os.path.exists(fpath):
                os.remove(fpath)
        self._files.clear()
        self._counter = 0


class ScrapyScheduler:
    """
    Scrapy 风格调度器

    核心流程:
      1. enqueue_request(): 去重 → 入队
      2. next_request():    出队 → 返回给 Engine
      3. 内存队列 + 可选磁盘队列

    类比 Scrapy 的 Scheduler 组件
    """

    def __init__(self, dupefilter: RFPDupeFilter = None,
                 disk_queue_path: str = None,
                 max_queue_size: int = 50000):
        self.dupefilter = dupefilter or RFPDupeFilter()
        self.mq: PriorityQueue = PriorityQueue()       # 内存队列
        self.dq: Optional[DiskQueue] = None             # 磁盘队列
        if disk_queue_path:
            self.dq = DiskQueue(disk_queue_path)
        self.max_queue_size = max_queue_size
        self.stats = {"enqueued": 0, "dequeued": 0, "filtered": 0}

    def enqueue_request(self, request: Request) -> bool:
        """
        将请求加入调度队列

        Returns:
            True  — 入队成功
            False — 被过滤（重复请求）
        """
        # 跳过 dont_filter 的请求
        if not request.dont_filter and self.dupefilter.request_seen(request):
            self.stats["filtered"] += 1
            logger.debug(f"请求被过滤: {request.url}")
            return False

        self.mq.push(request)
        self.stats["enqueued"] += 1

        # 内存队列满时换入磁盘
        if len(self.mq) > self.max_queue_size and self.dq:
            overflow = []
            for _ in range(len(self.mq) // 4):  # 移出 1/4
                req = self.mq.pop()
                if req:
                    overflow.append(req)
            for req in overflow:
                self.dq.push(req)

        return True

    def next_request(self) -> Optional[Request]:
        """获取下一个待处理的请求"""
        # 优先从内存队列取
        request = self.mq.pop()
        if request is None and self.dq:
            # 从磁盘队列取
            request = self.dq.pop()
        if request:
            self.stats["dequeued"] += 1
        return request

    def __len__(self) -> int:
        return len(self.mq) + (len(self.dq) if self.dq else 0)

    def close(self):
        if self.dq:
            self.dq.clear()
        self.mq.clear()
        self.dupefilter.clear()

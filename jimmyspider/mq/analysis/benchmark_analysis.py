"""
三种消息队列: 全面模拟测试 + 深度分析 + 量化对比

运行前提: 无需任何 MQ 服务，纯逻辑模拟测试

作为参考脚本保留（原 research lab 分析脚本，已适配 jimmyspider.mq 包结构）。
可直接运行: python jimmyspider/mq/analysis/benchmark_analysis.py
"""

import sys
import os
import time
import json
import inspect
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

# 定位 jimmySpider 根目录（本文件位于 jimmyspider/mq/analysis/ 下，向上 4 级），
# 使 jimmyspider.mq.* 包可导入（支持直接以脚本方式运行）
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from jimmyspider.mq.common.base import TaskMessage, MessageQueueProducer, MessageQueueConsumer


# ============================================================
# Part 1: 核心逻辑模拟测试（不依赖外部服务）
# ============================================================

class TestStats:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []

    def add(self, name: str, ok: bool, detail: str = ""):
        if ok:
            self.passed += 1
            self.results.append(("PASS", name, detail))
        else:
            self.failed += 1
            self.results.append(("FAIL", name, detail))

    def report(self):
        print("\n" + "=" * 70)
        print(f"  测试结果: {self.passed} 通过 / {self.failed} 失败 / {len(self.results)} 总计")
        print("=" * 70)
        for status, name, detail in self.results:
            print(f"  {status} {name}")
            if detail:
                print(f"     {detail}")


stats = TestStats()


# ---- 1.1 TaskMessage 序列化与反序列化 ----
def test_task_message_serialization():
    print("\n[1] TaskMessage 序列化测试")

    # 基本序列化
    t = TaskMessage(task_id="abc123", task_type="crawl", payload={"url": "https://test.com"})
    js = t.to_json()
    t2 = TaskMessage.from_json(js)

    assert t2.task_id == "abc123", f"task_id mismatch: {t2.task_id}"
    assert t2.task_type == "crawl", f"task_type mismatch: {t2.task_type}"
    assert t2.payload["url"] == "https://test.com", f"payload mismatch"
    assert isinstance(t2.created_at, float), f"created_at type: {type(t2.created_at)}"
    stats.add("基本序列化/反序列化", True)

    # 复杂 payload
    t3 = TaskMessage(
        task_id="complex",
        task_type="crawl_detail",
        payload={"url": "https://x.com", "headers": {"X-Token": "xxx"}, "retry": 3,
                 "meta": {"domain": "example.com", "proxy": "http://p:8080"}},
        priority=5, max_retries=5,
        metadata={"source": "list_page", "depth": 2}
    )
    js3 = t3.to_json()
    t4 = TaskMessage.from_json(js3)
    assert t4.payload["headers"]["X-Token"] == "xxx"
    assert t4.metadata["depth"] == 2
    assert t4.priority == 5
    stats.add("复杂 payload 序列化", True)

    # JSON 格式有效性
    parsed = json.loads(js3)
    assert "task_id" in parsed and "payload" in parsed and "metadata" in parsed
    stats.add("JSON 格式有效性", True)

    # 空 payload
    t5 = TaskMessage(task_id="empty", task_type="ping")
    js5 = t5.to_json()
    t6 = TaskMessage.from_json(js5)
    assert t6.payload == {}
    stats.add("空 payload 序列化", True)


# ---- 1.2 重试与死信逻辑 ----
def test_retry_logic():
    print("\n[2] 重试与死信逻辑测试")

    # 基本重试
    t = TaskMessage(task_id="r1", task_type="crawl", max_retries=3, retry_count=0)
    assert t.can_retry() == True
    t.increment_retry()
    assert t.retry_count == 1
    assert t.can_retry() == True
    t.increment_retry(); t.increment_retry()
    assert t.retry_count == 3
    assert t.can_retry() == False  # 已用完重试次数
    stats.add("重试次数边界 (0→3) ", True)

    # max_retries=0 永不重试
    t2 = TaskMessage(task_id="r2", task_type="crawl", max_retries=0)
    assert t2.can_retry() == False
    stats.add("max_retries=0 永不重试", True)

    # max_retries=1
    t3 = TaskMessage(task_id="r3", task_type="crawl", max_retries=1)
    assert t3.can_retry() == True
    t3.increment_retry()
    assert t3.can_retry() == False
    stats.add("max_retries=1 仅一次重试", True)

    # 大量重试
    t4 = TaskMessage(task_id="r4", task_type="crawl", max_retries=10)
    for _ in range(9):
        t4.increment_retry()
    assert t4.can_retry() == True
    t4.increment_retry()
    assert t4.can_retry() == False
    stats.add("max_retries=10 边界", True)


# ---- 1.3 接口一致性检查 ----
def test_interface_compliance():
    print("\n[3] 接口一致性检查")

    from jimmyspider.mq.redis_mq.producer import RedisProducer
    from jimmyspider.mq.redis_mq.consumer import RedisConsumer
    from jimmyspider.mq.kafka_mq.producer import KafkaProducer
    from jimmyspider.mq.kafka_mq.consumer import KafkaConsumer
    from jimmyspider.mq.rabbitmq_mq.producer import RabbitMQProducer
    from jimmyspider.mq.rabbitmq_mq.consumer import RabbitMQConsumer

    producers = [RedisProducer, KafkaProducer, RabbitMQProducer]
    consumers = [RedisConsumer, KafkaConsumer, RabbitMQConsumer]

    # 所有生产者继承自 MessageQueueProducer
    for cls in producers:
        assert issubclass(cls, MessageQueueProducer), f"{cls.__name__} 未继承 MessageQueueProducer"
    stats.add("所有 Producer 继承自基类", True)

    # 所有消费者继承自 MessageQueueConsumer
    for cls in consumers:
        assert issubclass(cls, MessageQueueConsumer), f"{cls.__name__} 未继承 MessageQueueConsumer"
    stats.add("所有 Consumer 继承自基类", True)

    # 生产者必须实现的方法
    required_producer_methods = ["connect", "_send", "close", "send", "send_batch"]
    for cls in producers:
        for method in required_producer_methods:
            # 检查自身或父类是否有该方法
            has = hasattr(cls, method)
            detail = f"{cls.__name__}.{method}()" if not has else ""
            stats.add(f"{cls.__name__} 实现 {method}()", has, detail)

    # 消费者必须实现的方法
    required_consumer_methods = ["connect", "_consume_loop", "_ack", "_nack", "close", "consume"]
    for cls in consumers:
        for method in required_consumer_methods:
            has = hasattr(cls, method)
            detail = f"{cls.__name__}.{method}()" if not has else ""
            stats.add(f"{cls.__name__} 实现 {method}()", has, detail)

    # 额外方法检查: 每种 Producer 是否有独特能力
    extra_checks = [
        (RedisProducer, "send_priority", "优先级发送"),
        (RedisProducer, "send_delayed", "延迟发送"),
        (RedisProducer, "queue_length", "队列长度查询"),
        (KafkaProducer, "send_with_key", "Key分区发送"),
        (KafkaProducer, "send_batch_async", "异步批量发送"),
        (KafkaProducer, "flush", "刷新缓冲区"),
        (RabbitMQProducer, "declare_queue", "声明队列"),
        (RabbitMQProducer, "send_with_routing", "路由发送"),
        (RabbitMQProducer, "send_delayed", "延迟发送"),
        (RedisConsumer, "replay_dead_letter", "死信重放"),
        (KafkaConsumer, "seek_to_beginning", "Offset重置到头"),
        (KafkaConsumer, "get_current_offset", "获取当前Offset"),
        (RabbitMQConsumer, "_on_message", "消息回调"),
    ]
    for cls, method, desc in extra_checks:
        has = hasattr(cls, method)
        detail = "" if has else f"{cls.__name__} 缺少 {method}()"
        stats.add(f"{cls.__name__}.{method}() — {desc}", has, detail)


# ---- 1.4 代码度量 ----
def test_code_metrics():
    print("\n[4] 代码度量")

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    modules = {
        "common": base_dir + "/common/base.py",
        "redis_producer": base_dir + "/redis_mq/producer.py",
        "redis_consumer": base_dir + "/redis_mq/consumer.py",
        "kafka_producer": base_dir + "/kafka_mq/producer.py",
        "kafka_consumer": base_dir + "/kafka_mq/consumer.py",
        "rabbitmq_producer": base_dir + "/rabbitmq_mq/producer.py",
        "rabbitmq_consumer": base_dir + "/rabbitmq_mq/consumer.py",
    }

    metrics = {}
    for name, path in modules.items():
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        code_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
        doc_lines = len([l for l in lines if l.strip().startswith('"""') or l.strip().startswith("#")])
        class_count = len([l for l in lines if l.strip().startswith("class ")])
        method_count = len([l for l in lines if l.strip().startswith("    def ")])
        metrics[name] = {
            "total_lines": len(lines),
            "code_lines": len(code_lines),
            "doc_lines": doc_lines,
            "class_count": class_count,
            "method_count": method_count,
        }
        print(f"  {name}: {len(lines)}行 | {class_count}类 | {method_count}方法 | 注释{doc_lines}行")

    # 验证每个模块至少有一个类
    for name, m in metrics.items():
        assert m["class_count"] >= 1, f"{name} 无类定义"
    stats.add("所有模块均有类定义", True)

    # Redis / Kafka / RabbitMQ 代码量合理性
    redis_total = metrics["redis_producer"]["total_lines"] + metrics["redis_consumer"]["total_lines"]
    kafka_total = metrics["kafka_producer"]["total_lines"] + metrics["kafka_consumer"]["total_lines"]
    rabbit_total = metrics["rabbitmq_producer"]["total_lines"] + metrics["rabbitmq_consumer"]["total_lines"]
    print(f"\n  代码总量: Redis={redis_total}行 Kafka={kafka_total}行 RabbitMQ={rabbit_total}行")

    stats.add("Redis 代码量 > 150 行", redis_total > 150, f"{redis_total}行")
    stats.add("Kafka 代码量 > 150 行", kafka_total > 150, f"{kafka_total}行")
    stats.add("RabbitMQ 代码量 > 150 行", rabbit_total > 150, f"{rabbit_total}行")

    return metrics


# ---- 1.5 模拟并发消费 ----
def test_simulated_concurrency():
    print("\n[5] 模拟并发消费")

    # 模拟: 多线程消费共享队列（List 模式逻辑模拟）
    class SimQueue:
        """模拟 Redis List / Kafka Partition / RabbitMQ Queue"""
        def __init__(self):
            self.messages = []
            self.dead_letter = []
            self.processed = []
            self._lock = threading.Lock()

        def push(self, task: TaskMessage):
            with self._lock:
                self.messages.append(task)

        def pop(self) -> TaskMessage | None:
            with self._lock:
                if self.messages:
                    return self.messages.pop(0)
                return None

        def to_dead(self, task: TaskMessage):
            with self._lock:
                self.dead_letter.append(task)

    queue = SimQueue()
    success_count = defaultdict(int)
    fail_count = defaultdict(int)

    def worker(worker_id: int):
        while True:
            task = queue.pop()
            if task is None:
                break
            # 模拟: worker 1 处理奇数失败, worker 2 全部成功
            task_id_num = int(task.task_id.split("_")[-1]) if "_" in task.task_id else 0
            if worker_id == 1 and task_id_num % 2 == 1:
                task.increment_retry()
                if task.can_retry():
                    queue.push(task)  # 重试
                else:
                    queue.to_dead(task)
                fail_count[worker_id] += 1
            else:
                success_count[worker_id] += 1

    # 投递 20 个任务
    for i in range(20):
        t = TaskMessage(task_id=f"task_{i}", task_type="crawl",
                       payload={"url": f"https://p/{i}"}, max_retries=2)
        queue.push(t)

    # 3 个 Worker 并发
    threads = []
    for wid in range(3):
        t = threading.Thread(target=worker, args=(wid,))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total = sum(success_count.values()) + sum(fail_count.values())
    print(f"  处理: {sum(success_count.values())} 成功 / {sum(fail_count.values())} 失败")
    print(f"  死信: {len(queue.dead_letter)} 条")
    print(f"  剩余: {len(queue.messages)} 条")

    # 所有消息都应被处理（成功 + 死信）
    # 注意: 失败消息的重试计入 fail_count，最终可能进死信
    assert len(queue.messages) == 0, f"队列未清空: {len(queue.messages)} 条剩余"
    stats.add("并发消费: 队列完全清空", True)

    assert total >= 20, f"处理的次数不足: {total}"
    stats.add(f"并发消费: 所有消息被处理 (≥20)", total >= 20)


# ---- 1.6 模拟优先级排序 ----
def test_simulated_priority():
    print("\n[6] 模拟优先级队列")

    tasks = [
        TaskMessage(task_id="low", task_type="c", priority=1),
        TaskMessage(task_id="mid", task_type="c", priority=5),
        TaskMessage(task_id="high", task_type="c", priority=9),
        TaskMessage(task_id="low2", task_type="c", priority=2),
        TaskMessage(task_id="mid2", task_type="c", priority=5),
        TaskMessage(task_id="high2", task_type="c", priority=8),
    ]

    # 模拟 Redis ZSET 的排序: 按 priority 降序
    sorted_tasks = sorted(tasks, key=lambda t: -t.priority)
    print(f"  原始顺序: {[t.task_id for t in tasks]}")
    print(f"  排序顺序: {[t.task_id for t in sorted_tasks]}")

    # 验证: 高优先级在前
    priorities = [t.priority for t in sorted_tasks]
    assert priorities == sorted(priorities, reverse=True), f"未按优先级排序: {priorities}"
    stats.add("优先级降序正确", True)

    # 验证: 同优先级保持插入顺序（稳定排序）
    mid_indices = [i for i, t in enumerate(sorted_tasks) if t.task_id.startswith("mid")]
    assert mid_indices[0] < mid_indices[1], "同优先级应保持原序"
    stats.add("同优先级稳定排序", True)


# ---- 1.7 模拟延迟队列 ----
def test_simulated_delayed():
    print("\n[7] 模拟延迟队列")

    deliver_at = {}
    ready = []
    delayed = []

    # 投递 3 条延迟消息
    now = time.time()
    tasks = [
        ("t1", 0.1),   # 100ms 延迟
        ("t2", 0.3),   # 300ms 延迟
        ("t3", 0.5),   # 500ms 延迟
    ]
    for tid, delay in tasks:
        deliver_at[tid] = now + delay
        delayed.append(tid)

    # 模拟时间推进
    for check_time in [0.05, 0.15, 0.35, 0.55]:
        current = now + check_time
        still_delayed = []
        for tid in delayed:
            if current >= deliver_at[tid]:
                ready.append(tid)
            else:
                still_delayed.append(tid)
        delayed = still_delayed
        # 通知
        if ready:
            print(f"  t={check_time:.2f}s: {ready} 到期")
            ready = []

    assert len(delayed) == 0, f"延迟消息未全部到期: {delayed}"
    stats.add("延迟消息全部到期", True)


# ---- 1.8 模拟 Topic Exchange 路由 ----
def test_simulated_routing():
    print("\n[8] 模拟 Topic Exchange 路由")

    import re

    # 绑定规则
    bindings = {
        "crawl.list.#": ["list_queue"],
        "crawl.detail.#": ["detail_queue"],
        "crawl.#": ["all_queue"],
        "download.#.image": ["image_queue"],
    }

    def match_routing(pattern: str, routing_key: str) -> bool:
        """将 RabbitMQ pattern 转为正则"""
        parts = pattern.split(".")
        regex_parts = []
        for p in parts:
            if p == "#":
                regex_parts.append(r".*")
            elif p == "*":
                regex_parts.append(r"[^.]+")
            else:
                regex_parts.append(re.escape(p))
        regex = "^" + r"\.".join(regex_parts) + "$"
        return bool(re.match(regex, routing_key))

    # 测试消息
    test_msgs = [
        ("crawl.list.baidu", {"list_queue", "all_queue"}),
        ("crawl.detail.baidu.001", {"detail_queue", "all_queue"}),
        ("crawl.list.taobao", {"list_queue", "all_queue"}),
        ("download.avatar.image", {"image_queue"}),
        ("other.event", set()),  # 不匹配任何队列
    ]

    for routing_key, expected_queues in test_msgs:
        matched = set()
        for pattern, queues in bindings.items():
            if match_routing(pattern, routing_key):
                matched.update(queues)
        ok = matched == expected_queues
        detail = f"{routing_key} → {matched} (期望 {expected_queues})"
        if not ok:
            print(f"  FAIL: {detail}")
        stats.add(f"路由: {routing_key}", ok, detail)

    # 通配符测试
    assert match_routing("crawl.*.baidu", "crawl.list.baidu") == True
    assert match_routing("crawl.*.baidu", "crawl.detail.baidu") == True
    assert match_routing("crawl.*.baidu", "crawl.list.taobao") == False  # 不匹配
    assert match_routing("crawl.#", "crawl.a.b.c.d") == True  # 匹配任意深度
    stats.add("通配符 * 匹配单段", True)
    stats.add("通配符 # 匹配多段", True)


# ---- 1.9 模拟 Key-based 分区 ----
def test_simulated_partition():
    print("\n[9] 模拟 Kafka Key-based 分区")

    def partition(key: str, num_partitions: int) -> int:
        """Kafka 默认分区器: hash(key) % num_partitions"""
        return hash(key) % num_partitions

    # 相同 key → 同一分区
    keys = ["baidu.com", "baidu.com", "baidu.com", "taobao.com", "taobao.com", "jd.com"]
    partitions = [partition(k, 3) for k in keys]

    # 验证相同 key 进入同一分区
    baidu_ps = [p for k, p in zip(keys, partitions) if k == "baidu.com"]
    taobao_ps = [p for k, p in zip(keys, partitions) if k == "taobao.com"]
    assert len(set(baidu_ps)) == 1, f"baidu 应全在同一分区: {baidu_ps}"
    assert len(set(taobao_ps)) == 1, f"taobao 应全在同一分区: {taobao_ps}"
    stats.add("相同 Key → 同一分区 (baidu)", True)
    stats.add("相同 Key → 同一分区 (taobao)", True)

    print(f"  分区分布: {dict(zip(keys, partitions))}")

    # 验证分区分布（不一定均匀，但应在范围内）
    for p in partitions:
        assert 0 <= p < 3, f"分区越界: {p}"
    stats.add("所有分区索引在 [0, 3) 范围内", True)


# ---- 1.10 模拟 Stream 消费者组 ----
def test_simulated_consumer_group():
    print("\n[10] 模拟 Stream 消费者组")

    # 模拟: 3 个消费者组各含 N 个消费者，验证组间隔离
    class StreamSimulator:
        def __init__(self):
            self.stream = []  # (msg_id, data)
            self.consumer_groups = {}  # group_name -> {consumer_name: last_id}
            self.msg_counter = 0

        def add(self, data: str):
            self.msg_counter += 1
            self.stream.append((f"{self.msg_counter}-0", data))

        def read_group(self, group: str, consumer: str, count: int = 1):
            if group not in self.consumer_groups:
                self.consumer_groups[group] = {}
            last_id = self.consumer_groups[group].get(consumer, "0-0")
            # 查找 last_id 之后的消息
            results = []
            for msg_id, data in self.stream:
                if msg_id > last_id:
                    results.append((msg_id, data))
                    if len(results) >= count:
                        break
            if results:
                last_id = results[-1][0]
                self.consumer_groups[group][consumer] = last_id
            return results

    sim = StreamSimulator()
    for i in range(10):
        sim.add(f"msg_{i}")

    # Group A 的 consumer_1 消费 5 条
    g1 = sim.read_group("group_a", "c1", 5)
    print(f"  Group A / c1: {len(g1)} 条")

    # Group A 的 consumer_2 消费 3 条（同组共享进度？简化模型下独立）
    g2 = sim.read_group("group_a", "c2", 3)
    print(f"  Group A / c2: {len(g2)} 条")

    # Group B 从头消费（独立组，可以消费全部）
    g3 = sim.read_group("group_b", "c1", 10)
    print(f"  Group B / c1: {len(g3)} 条")

    # 验证组间隔离: Group B 可以消费前 10 条
    assert len(g3) == 10, f"Group B 应能消费全部 10 条，实际 {len(g3)}"
    stats.add("Stream 消费者组隔离 (Group B 看到全部)", True)

    # 验证同组不同消费者有独立进度
    assert len(g1) == 5 and len(g2) == 3
    stats.add("同组消费者独立进度", True)


# ---- 1.11 模拟 ACK / NACK / 死信链路 ----
def test_simulated_ack_nack():
    print("\n[11] 模拟 ACK/NACK 死信链路")

    pending = {}  # delivery_tag -> (raw, retry_count)
    dead = []
    acked = []

    def process(delivery_tag: int, succeed: bool, max_retries: int = 3):
        raw, retries = pending[delivery_tag]
        if succeed:
            acked.append(delivery_tag)
            del pending[delivery_tag]
            return "ACK"
        else:
            if retries < max_retries:
                pending[delivery_tag] = (raw, retries + 1)
                return f"RETRY ({retries+1}/{max_retries})"
            else:
                dead.append(raw)
                del pending[delivery_tag]
                return "DEAD"

    # 加载测试数据
    for i in range(5):
        pending[i] = (f"task_{i}", 0)

    # task_0, task_1, task_3 成功; task_2 失败 3 次进死信; task_4 失败 1 次后成功
    results = []
    for _ in range(4):  # max_retries=3 → 需失败 4 次才进死信
        if 2 in pending:
            results.append(process(2, False))
    results.append(process(0, True))
    results.append(process(1, True))
    results.append(process(3, True))
    for _ in range(2):
        if 4 in pending:
            results.append(process(4, False))
    if 4 in pending:
        results.append(process(4, True))

    print(f"  ACK: {len(acked)} | Dead: {len(dead)} | Pending: {len(pending)}")
    print(f"  结果序列: {results}")

    assert len(acked) == 4, f"应有 4 条 ACK: {len(acked)}"
    assert len(dead) == 1, f"应有 1 条死信: {len(dead)}"
    assert len(pending) == 0, f"pending 应为空: {len(pending)}"
    assert "task_2" in dead[0], "task_2 应进死信"
    stats.add("ACK/NACK 死信链路完整", True)


# ============================================================
# Part 2: 深度代码分析
# ============================================================

def analyze_architecture():
    """分析三种实现的架构差异"""
    print("\n" + "=" * 70)
    print("  Part 2: 深度代码分析")
    print("=" * 70)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    implementations = {
        "Redis": ("redis_mq/producer.py", "redis_mq/consumer.py"),
        "Kafka": ("kafka_mq/producer.py", "kafka_mq/consumer.py"),
        "RabbitMQ": ("rabbitmq_mq/producer.py", "rabbitmq_mq/consumer.py"),
    }

    analysis = {}

    for name, (prod_file, cons_file) in implementations.items():
        with open(os.path.join(base_dir, prod_file), "r", encoding="utf-8") as f:
            prod_code = f.read()
        with open(os.path.join(base_dir, cons_file), "r", encoding="utf-8") as f:
            cons_code = f.read()

        prod_lines = len(prod_code.split("\n"))
        cons_lines = len(cons_code.split("\n"))

        # 统计功能特性
        features = []
        if "priority" in (prod_code + cons_code).lower() or "zadd" in (prod_code + cons_code).lower():
            features.append("优先级队列")
        if "delayed" in (prod_code + cons_code).lower() or "delay" in (prod_code + cons_code).lower():
            features.append("延迟队列")
        if "dead" in (prod_code + cons_code).lower():
            features.append("死信队列")
        if "replay" in (prod_code + cons_code).lower():
            features.append("死信重放")
        if "batch" in prod_code.lower():
            features.append("批量发送")
        if "group" in cons_code.lower() or "group" in prod_code.lower():
            features.append("消费者组")
        if "key" in prod_code.lower() and "partition" in prod_code.lower():
            features.append("Key分区有序")
        if "routing" in prod_code.lower() or "exchange" in prod_code.lower():
            features.append("灵活路由")
        if "offset" in cons_code.lower():
            features.append("Offset管理")
        if "ack" in cons_code.lower() or "confirm" in prod_code.lower():
            features.append("消息确认机制")
        if "durable" in prod_code.lower() or "persist" in (prod_code + cons_code).lower():
            features.append("消息持久化")
        if "qos" in cons_code.lower() or "prefetch" in cons_code.lower():
            features.append("QoS流控")
        if "stream" in (prod_code + cons_code).lower():
            features.append("Stream模式")
        if "list" in prod_code.lower() and "brpop" in cons_code.lower():
            features.append("List阻塞模式")

        # 连接模式
        conn_mode = []
        if "BlockingConnection" in prod_code or "BlockingConnection" in cons_code:
            conn_mode.append("同步阻塞")
        if "SelectConnection" in prod_code:
            conn_mode.append("异步回调")

        analysis[name] = {
            "prod_lines": prod_lines,
            "cons_lines": cons_lines,
            "total_lines": prod_lines + cons_lines,
            "features": features,
            "conn_mode": conn_mode or ["持久连接"],
        }

    # 打印分析结果
    print(f"\n{'指标':<20} {'Redis':>10} {'Kafka':>10} {'RabbitMQ':>10}")
    print("-" * 55)
    print(f"{'生产者代码行数':<20} {analysis['Redis']['prod_lines']:>10} {analysis['Kafka']['prod_lines']:>10} {analysis['RabbitMQ']['prod_lines']:>10}")
    print(f"{'消费者代码行数':<20} {analysis['Redis']['cons_lines']:>10} {analysis['Kafka']['cons_lines']:>10} {analysis['RabbitMQ']['cons_lines']:>10}")
    print(f"{'总代码量':<20} {analysis['Redis']['total_lines']:>10} {analysis['Kafka']['total_lines']:>10} {analysis['RabbitMQ']['total_lines']:>10}")
    print(f"{'功能数':<20} {len(analysis['Redis']['features']):>10} {len(analysis['Kafka']['features']):>10} {len(analysis['RabbitMQ']['features']):>10}")

    print(f"\n  功能详情:")
    for name, data in analysis.items():
        print(f"    {name}: {', '.join(data['features'])}")

    return analysis


def compare_api_surface():
    """对比 API 接口表面积"""
    print("\n" + "-" * 55)
    print("  API 接口对比")
    print("-" * 55)

    from jimmyspider.mq.redis_mq.producer import RedisProducer
    from jimmyspider.mq.redis_mq.consumer import RedisConsumer
    from jimmyspider.mq.kafka_mq.producer import KafkaProducer
    from jimmyspider.mq.kafka_mq.consumer import KafkaConsumer
    from jimmyspider.mq.rabbitmq_mq.producer import RabbitMQProducer
    from jimmyspider.mq.rabbitmq_mq.consumer import RabbitMQConsumer

    producers = {
        "Redis": RedisProducer,
        "Kafka": KafkaProducer,
        "RabbitMQ": RabbitMQProducer,
    }
    consumers = {
        "Redis": RedisConsumer,
        "Kafka": KafkaConsumer,
        "RabbitMQ": RabbitMQConsumer,
    }

    def get_public_methods(cls):
        return [m for m in dir(cls) if not m.startswith("_") and callable(getattr(cls, m))
                and m not in dir(MessageQueueProducer)]

    def get_init_params(cls):
        sig = inspect.signature(cls.__init__)
        return [p for p in sig.parameters if p not in ("self", "kwargs", "args")]

    print("\n  生产者独有方法:")
    for name, cls in producers.items():
        methods = get_public_methods(cls)
        print(f"    {name}: {methods}")

    print("\n  消费者独有方法:")
    for name, cls in consumers.items():
        methods = [m for m in dir(cls) if not m.startswith("_") and callable(getattr(cls, m))
                    and m not in dir(MessageQueueConsumer)]
        print(f"    {name}: {methods}")

    print("\n  初始化参数数量:")
    for name, cls in {**producers, **consumers}.items():
        params = get_init_params(cls)
        print(f"    {name}: {len(params)} 个 — {params}")


# ============================================================
# Part 3: 综合对比报告
# ============================================================

def generate_comparison_report():
    print("\n" + "=" * 70)
    print("  Part 3: 综合对比报告")
    print("=" * 70)

    # 量化评分
    dimensions = {
        "功能丰富度":     {"Redis": 9, "Kafka": 6, "RabbitMQ": 9},
        "代码简洁性":     {"Redis": 8, "Kafka": 7, "RabbitMQ": 6},
        "吞吐潜力":       {"Redis": 7, "Kafka": 10, "RabbitMQ": 5},
        "部署易用性":     {"Redis": 10, "Kafka": 4, "RabbitMQ": 6},
        "爬虫适配度":     {"Redis": 10, "Kafka": 7, "RabbitMQ": 8},
        "可观测性":       {"Redis": 3, "Kafka": 5, "RabbitMQ": 9},
        "消息可靠性":     {"Redis": 5, "Kafka": 10, "RabbitMQ": 10},
        "路由灵活性":     {"Redis": 2, "Kafka": 3, "RabbitMQ": 10},
        "学习曲线(越低越好)": {"Redis": 10, "Kafka": 4, "RabbitMQ": 6},
        "Spider已有生态": {"Redis": 10, "Kafka": 5, "RabbitMQ": 5},
    }

    print(f"\n  {'维度':<22} {'Redis':>6} {'Kafka':>6} {'RabbitMQ':>6}")
    print("  " + "-" * 44)
    for dim, scores in dimensions.items():
        print(f"  {dim:<22} {scores['Redis']:>6} {scores['Kafka']:>6} {scores['RabbitMQ']:>6}")

    # 加权总分
    weights = {
        "功能丰富度": 0.15,
        "代码简洁性": 0.05,
        "吞吐潜力": 0.05,
        "部署易用性": 0.15,
        "爬虫适配度": 0.25,
        "可观测性": 0.05,
        "消息可靠性": 0.10,
        "路由灵活性": 0.10,
        "学习曲线(越低越好)": 0.05,
        "Spider已有生态": 0.05,
    }
    totals = {"Redis": 0, "Kafka": 0, "RabbitMQ": 0}
    for dim, w in weights.items():
        for mq in totals:
            totals[mq] += dimensions[dim][mq] * w

    print(f"\n  {'加权总分':<22} {totals['Redis']:>6.1f} {totals['Kafka']:>6.1f} {totals['RabbitMQ']:>6.1f}")

    # 场景推荐
    print("\n  ┌─────────────────────────────────────────────────┐")
    print("  │              场景推荐决策树                       │")
    print("  ├─────────────────────────────────────────────────┤")
    print("  │ 简单任务队列 (3 Worker 以内) → Redis List       │")
    print("  │ 需要优先级/延迟队列          → Redis ZSET       │")
    print("  │ 需要消费者组 + 负载均衡       → Kafka / Redis    │")
    print("  │                                                │")
    print("  │ 中小规模 + 需灵活路由         → RabbitMQ        │")
    print("  │ 中小规模 + 需管理界面         → RabbitMQ        │")
    print("  │ 中小规模 + 已有 Redis         → Redis           │")
    print("  │                                                │")
    print("  │ 大规模分布式 (>100万/天)      → Kafka           │")
    print("  │ 需要消息回溯 + 重放           → Kafka           │")
    print("  │ 需要严格顺序(同站有序)        → Kafka           │")
    print("  │ 需要大数据生态对接            → Kafka           │")
    print("  └─────────────────────────────────────────────────┘")

    return totals


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("  三种消息队列 — 全面模拟测试 + 深度分析 + 对比报告")
    print("  (纯逻辑验证，无需任何 MQ 服务)")
    print("=" * 70)

    # Part 1: 测试
    print("\n" + "─" * 70)
    print("  Part 1: 核心逻辑模拟测试 (11 组)")
    print("─" * 70)

    test_task_message_serialization()
    test_retry_logic()
    test_interface_compliance()
    test_code_metrics()
    test_simulated_concurrency()
    test_simulated_priority()
    test_simulated_delayed()
    test_simulated_routing()
    test_simulated_partition()
    test_simulated_consumer_group()
    test_simulated_ack_nack()

    stats.report()

    # Part 2: 分析
    analyze_architecture()
    compare_api_surface()

    # Part 3: 对比
    totals = generate_comparison_report()

    # 最终判定
    print("\n" + "=" * 70)
    winner = max(totals, key=totals.get)
    print(f"  综合推荐: {winner} (得分 {totals[winner]:.1f})")
    print(f"  — 对 Spider 项目而言，{winner} 是最佳默认选择")
    print("=" * 70)

    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

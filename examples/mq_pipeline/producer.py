"""
mq_pipeline —— 消息队列「列表/详情分离」流水线 · 生产者

演示 jimmyspider.mq 的经典爬虫拆分模式：
  列表爬虫(本文件) 只负责抓列表页、生成详情任务 → 投递到消息队列
  详情爬虫(consumer.py) 负责消费任务、抓详情页、入库
  —— 两端解耦，可分别扩缩容、跨机器部署。

两种运行模式（-m/--mode 参数）：
  mock  （默认）  不需要任何外部服务：
                  - 列表页使用内置 mock HTML（演示用，不联网）
                  - 队列使用进程内内存队列 MemoryQueue（替代 Redis）
                  - 投递后进程内直接调用 consumer 的消费逻辑跑完整流水线
  redis           需要本机/远程 Redis：
                  - 使用 jimmyspider.mq.RedisProducer，mode="list"（LPUSH 入队）
                  - 只投递不消费，由 consumer.py --mode redis 单独消费

说明：mock 模式下共享的演示数据/内存队列定义在本文件，
consumer.py 通过 `from producer import ...` 复用，保证两端逻辑一致。
"""

import argparse
import sys
import threading
import time
import uuid

# Windows GBK 控制台无法打印中文/emoji，统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TOPIC = "mq_pipeline:tasks"          # 队列主题名
DETAIL_BASE = "https://news.example.com/detail/"

# ----------------------------------------------------------------------
# mock 专用：进程内内存队列（模拟 Redis List 的 LPUSH + BRPOP）
# 真实模式使用 jimmyspider.mq.RedisProducer / RedisConsumer
# ----------------------------------------------------------------------

class MemoryQueue:
    """极简内存队列：模拟 Redis List 模式（左进右出 FIFO）"""

    def __init__(self):
        self._items = []
        self._lock = threading.Lock()

    def push(self, raw: str) -> None:          # 模拟 LPUSH
        with self._lock:
            self._items.insert(0, raw)

    def pop(self, timeout: float = 0.0):       # 模拟 BRPOP（阻塞式）
        deadline = time.time() + timeout
        while True:
            with self._lock:
                if self._items:
                    return self._items.pop()
            if time.time() >= deadline:
                return None
            time.sleep(0.01)

    def __len__(self):
        with self._lock:
            return len(self._items)

# ----------------------------------------------------------------------
# mock 专用：内置列表页 HTML（演示「抓列表 → 生成任务」不依赖网络）
# ----------------------------------------------------------------------

MOCK_LIST_HTML = """<html><head><title>演示新闻列表</title></head><body>
<div class="list">
  <div class="item"><a href="{base}1">城市更新三年行动方案发布</a></div>
  <div class="item"><a href="{base}2">新能源汽车产销两旺</a></div>
  <div class="item"><a href="{base}3">老旧小区改造进入收尾阶段</a></div>
  <div class="item"><a href="{base}4">某商品页链接（模拟永远失败的任务）</a></div>
  <div class="item"><a href="{base}5">秋季文旅消费季启动</a></div>
</div>
</body></html>""".format(base=DETAIL_BASE)


def build_tasks() -> list:
    """
    抓列表页 → 提取链接 → 生成 TaskMessage 列表

    真实场景：用 jimmyspider.soup.extractSoup 或 requests 抓真实列表页。
    """
    from bs4 import BeautifulSoup
    from jimmyspider.mq import TaskMessage

    soup = BeautifulSoup(MOCK_LIST_HTML, "html.parser")
    tasks = []
    for i, a in enumerate(soup.select("div.item > a"), start=1):
        url = a.get("href")
        tasks.append(TaskMessage(
            task_id=f"detail-{i}-{uuid.uuid4().hex[:6]}",
            task_type="crawl_detail",
            payload={"url": url, "title": a.get_text(strip=True)},
            priority=5,
            max_retries=3,
            metadata={"source": "mock_list_page"},
        ))
    return tasks


def send_to_memory(tasks: list, queue: MemoryQueue) -> None:
    """mock 模式投递：入内存队列（模拟 LPUSH）"""
    for task in tasks:
        queue.push(task.to_json())
        print(f"[Producer] 已投递（内存队列） task_id={task.task_id} "
              f"url={task.payload['url']}")

def send_to_redis(tasks: list, topic: str) -> None:
    """redis 模式投递：jimmyspider.mq.RedisProducer，mode='list'（LPUSH）"""
    from jimmyspider.mq import RedisProducer
    try:
        producer = RedisProducer(mode="list")  # host/port/db 默认取 jimmyspider 配置
        producer.connect()
        try:
            result = producer.send_batch(topic, tasks)
            print(f"[Producer] 投递完成（Redis List 模式）: {result}")
            print(f"[Producer] 队列长度: {producer.queue_length(topic)}")
        finally:
            producer.close()
    except Exception as e:
        print(f"[Producer] Redis 连接失败: {e}")
        print("[Producer] 请先启动 Redis（或改用 -m mock 离线演示）")


def main():
    parser = argparse.ArgumentParser(description="mq_pipeline 生产者（列表 → 任务消息）")
    parser.add_argument("-m", "--mode", choices=["mock", "redis"], default="mock",
                        help="mock=内存队列(无需外部服务,默认)；redis=Redis List 队列")
    args = parser.parse_args()

    print("=" * 60)
    print(f"[Producer] 抓取列表页 → 生成 {len(build_tasks())} 个详情任务 "
          f"（模式: {args.mode}）")
    print("=" * 60)

    tasks = build_tasks()

    if args.mode == "redis":
        send_to_redis(tasks, TOPIC)
        print("\n[Producer] 投递完成。请另开终端运行: python consumer.py -m redis")
        return

    # ---- mock 模式：内存队列 + 进程内完整流水线演示 ----
    queue = MemoryQueue()
    send_to_memory(tasks, queue)

    # 投递完成后，进程内直接启动消费端，跑完整流水线
    # （真实部署中，consumer.py 是独立进程/机器）
    print("\n[Producer] 投递完成。进程内启动消费端跑完整流水线…")
    print("-" * 60)
    from consumer import consume_from_queue
    consume_from_queue(queue, topic=TOPIC)


if __name__ == "__main__":
    main()

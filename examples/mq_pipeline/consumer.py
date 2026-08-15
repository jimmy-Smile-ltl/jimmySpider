"""
mq_pipeline —— 消息队列「列表/详情分离」流水线 · 消费者

与 producer.py 配对使用：
  生产者把「详情页任务」投递到队列，本文件消费任务 → 抓详情页 → 入库。

两种运行模式（-m/--mode 参数）：
  mock  （默认）  不需要任何外部服务：
                  - 队列为进程内内存队列（MemoryQueue，定义在 producer.py）
                  - 若队列为空，自动用与 producer 相同的 mock 列表注入演示任务
                  - 详情页使用内置 mock HTML，入库到本地 SQLite（stdlib sqlite3）
                  - 完整演示 重试（指数退避）→ 死信 的处理链路
  redis           需要本机/远程 Redis：
                  - 使用 jimmyspider.mq.RedisConsumer，mode="list"（BRPOP）
                  - 消费 producer.py -m redis 投递的任务
                  - 失败重试 / 死信由 RedisConsumer 内置机制处理

mock 模式下故意模拟了 2 类失败任务，用于观察重试与死信：
  /detail/3  第一次处理失败 → 重试后成功（flaky）
  /detail/4  永远失败 → 超过 max_retries 后进入死信（dead letter）
"""

import argparse
import sys
import threading
import time

# Windows GBK 控制台无法打印中文/emoji，统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from producer import DETAIL_BASE, MemoryQueue, TOPIC

# ----------------------------------------------------------------------
# mock 专用：内置详情页 HTML + 「入库」到本地 SQLite（不依赖外部数据库）
# ----------------------------------------------------------------------

def fetch_mock_detail(url: str) -> str:
    """
    按任务 URL 生成详情页 mock HTML。

    真实场景：替换为 jimmyspider.request.SingleRequestHandler 抓取真实详情页。
    """
    n = url.rsplit("/", 1)[-1]
    return f"""<html><head><title>演示详情 {n}</title></head><body>
<article class="article-content">
  <h1>新闻标题 {n}</h1>
  <div class="article-meta"><span class="date">2026-08-15</span></div>
  <p>这是第 {n} 条新闻的正文内容，来自内置 mock 详情页，用于演示消息队列流水线。
  真实场景中此处是爬虫抓取并解析后的结构化数据。</p>
</article>
</body></html>"""

def save_to_db(item: dict, db_path: str = "pipeline.db") -> None:
    """将详情数据写入 SQLite（演示「入库」；真实项目可换 jimmyspider.mongo）"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS items "
            "(id TEXT PRIMARY KEY, title TEXT, url TEXT, saved_at TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO items (id, title, url, saved_at) VALUES (?, ?, ?, ?)",
            (item["id"], item["title"], item["url"], item["saved_at"]),
        )
        conn.commit()
    finally:
        conn.close()

# ----------------------------------------------------------------------
# 消息处理：解析详情页 → 组装数据 → 入库
# ----------------------------------------------------------------------

FAILED_URLS = {f"{DETAIL_BASE}4"}                      # 永远失败 → 死信
FLAKY_URLS = {f"{DETAIL_BASE}3"}                       # 第一次失败 → 重试后成功
_flaky_attempted = set()

def handle_task(task) -> bool:
    """
    处理单个详情任务。返回 True=成功 / False=失败（触发重试或死信）。

    mock 模式：抓 mock 详情页 → 解析 → 写 SQLite。
    真实场景：requests 抓详情 → extractSoup 解析 → HandleMongoDB 入库。
    """
    url = task.payload["url"]

    # ---- 模拟失败场景 ----
    if url in FAILED_URLS:
        print(f"  [Consumer] 处理失败 {url}（模拟：永远失败的任务）")
        return False
    if url in FLAKY_URLS and url not in _flaky_attempted:
        _flaky_attempted.add(url)
        print(f"  [Consumer] 处理失败 {url}（模拟：网络抖动，第一次失败）")
        return False

    # ---- 正常处理 ----
    html = fetch_mock_detail(url)
    n = url.rsplit("/", 1)[-1]
    title = html[html.find("<h1>") + 4: html.find("</h1>")]   # 简化解析
    item = {
        "id": task.task_id,
        "title": f"{title}（{task.payload['title']}）",
        "url": url,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_to_db(item)
    print(f"  [Consumer] 处理成功 task_id={task.task_id} → 已入库 {item['title']}")
    return True

# ----------------------------------------------------------------------
# mock 模式：内存队列消费循环（重试 → 死信，逻辑与 RedisConsumer 一致）
# ----------------------------------------------------------------------

def consume_from_queue(queue: MemoryQueue, topic: str,
                       max_retries: int = 3, retry_base_delay: float = 0.05) -> None:
    """
    消费内存队列中的任务，模拟 jimmyspider.mq.RedisConsumer 的处理语义：
      成功 → ack；失败 → 重试次数+1，指数退避（2^n 倍延迟）后重新入队；
      重试耗尽 → 进入死信队列。
    """
    from jimmyspider.mq import TaskMessage

    reset_db()                # 演示用：每次消费前清空上次的入库结果
    dead_letter = []          # 模拟 {topic}_dead 死信队列
    stats = {"succeed": 0, "retried": 0, "dead": 0}
    while True:
        raw = queue.pop(timeout=1.0)
        if raw is None:       # 队列清空且重试队列为空
            break

        task = TaskMessage.from_json(raw)
        ok = handle_task(task)
        if ok:
            stats["succeed"] += 1
            continue

        task.increment_retry()
        if task.can_retry():
            delay = retry_base_delay * (2 ** task.retry_count)   # 指数退避
            print(f"  [Consumer] 重试 task_id={task.task_id} "
                  f"第 {task.retry_count}/{task.max_retries} 次，{delay:.2f}s 后重新入队")
            time.sleep(delay)
            queue.push(task.to_json())                            # 重新入队
            stats["retried"] += 1
        else:
            dead_letter.append(task)
            stats["dead"] += 1
            print(f"  [Consumer] 进入死信 task_id={task.task_id} "
                  f"url={task.payload['url']}（超过最大重试次数 {task.max_retries}）")

    print("-" * 60)
    print(f"[Consumer] 消费完成: 成功 {stats['succeed']} | 重试 {stats['retried']} | "
          f"死信 {stats['dead']}")
    for task in dead_letter:
        print(f"  [DeadLetter] {task.task_id} {task.payload['url']}")

    import sqlite3
    conn = sqlite3.connect("pipeline.db")
    rows = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    conn.close()
    print(f"[Consumer] SQLite 已入库 {rows} 条详情 (pipeline.db)")

def reset_db(db_path: str = "pipeline.db") -> None:
    """每次演示前重置 SQLite 表，保证输出可重复"""
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("DROP TABLE IF EXISTS items")
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# redis 模式：jimmyspider.mq.RedisConsumer（BRPOP 阻塞消费）
# ----------------------------------------------------------------------

def consume_from_redis(topic: str, seconds: int = 8) -> None:
    """
    用 RedisConsumer 消费真实 Redis 队列（mode="list"）。

    失败重试 / 死信由框架自动处理：
      - 处理失败 → 写入 {topic}:retry_zset，按 2^retry 秒指数退避后重投
      - 重试耗尽 → 写入 {topic}_dead 死信队列（可用 replay_dead_letter 重放）
    这里所有任务都成功（重试/死信链路已在 mock 模式完整演示）。
    """
    from jimmyspider.mq import RedisConsumer

    processed = {"count": 0}
    done = threading.Event()

    def handler(task):
        ok = handle_task(task)
        if ok:
            processed["count"] += 1
            if processed["count"] >= 5:     # 全部处理完，提前退出
                done.set()
        return ok

    try:
        consumer = RedisConsumer(
            mode="list",
            block_ms=1000,                  # BRPOP 阻塞 1s
            max_retries=3,                  # 最大重试次数
            dead_letter_suffix="_dead",     # 死信队列后缀
        )
        consumer.connect()                  # 提前连接，失败可被捕获并提示
        print(f"[Consumer] 开始消费 topic={topic}（阻塞 {seconds}s 后自动退出）…")
        t = threading.Thread(target=consumer.consume, args=(topic, handler), daemon=True)
        t.start()
        done.wait(timeout=seconds)          # 处理完或超时退出
        consumer.stop()
        t.join(timeout=2)
    except Exception as e:
        print(f"[Consumer] Redis 连接失败: {e}")
        print("[Consumer] 请先启动 Redis（或改用 -m mock 离线演示）")
        return
    print(f"[Consumer] 共成功处理 {processed['count']} 个任务")


def main():
    parser = argparse.ArgumentParser(description="mq_pipeline 消费者（任务 → 详情 → 入库）")
    parser.add_argument("-m", "--mode", choices=["mock", "redis"], default="mock",
                        help="mock=内存队列(默认)；redis=消费 Redis 队列")
    parser.add_argument("--seconds", type=int, default=8,
                        help="redis 模式最长消费时长（秒）")
    args = parser.parse_args()

    print("=" * 60)
    print(f"[Consumer] 启动消费端（模式: {args.mode}）")
    print("=" * 60)

    if args.mode == "redis":
        consume_from_redis(TOPIC, args.seconds)
        return

    # ---- mock 模式 ----
    queue = MemoryQueue()

    # 单独运行 consumer 时队列为空，注入与 producer 相同的演示任务
    from producer import build_tasks
    print("[Consumer] 内存队列为空，自动注入演示任务（与 producer.py 相同）…")
    for task in build_tasks():
        queue.push(task.to_json())
    print(f"[Consumer] 已注入 {len(queue)} 个任务，开始消费…")
    print("-" * 60)
    consume_from_queue(queue, topic=TOPIC)


if __name__ == "__main__":
    main()

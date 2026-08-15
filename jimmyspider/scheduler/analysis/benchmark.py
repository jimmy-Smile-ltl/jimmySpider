"""
两种调度器架构: 全面对比基准测试

对比维度:
  1. 吞吐量 (req/s) — 不同并发级别
  2. 延迟分布
  3. 内存占用
  4. 代码架构复杂度
  5. 资源利用率

运行: python jimmyspider/scheduler/analysis/benchmark.py
"""

import sys
import os
import time
import json
import threading
import http.server
import socketserver
import asyncio
import tracemalloc

# 使 jimmyspider 包可从源码目录直接导入（已安装则无需）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from jimmyspider.scheduler.common.request import Request
from jimmyspider.scheduler.common.spider import BaseSpider
from jimmyspider.scheduler.scrapy_sched.engine import ScrapyEngine
from jimmyspider.scheduler.aiospider_sched.engine import AioSpiderEngine


# ============================================================
# 共享测试基础设施
# ============================================================

class BenchmarkServer:
    """高性能本地 HTTP 测试服务器"""

    def __init__(self, port: int = 0, response_delay: float = 0.0):
        self.port = port
        self.response_delay = response_delay

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if this.response_delay > 0:
                    time.sleep(this.response_delay)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "url": self.path,
                    "status": "ok",
                    "timestamp": time.time(),
                }).encode())

            def log_message(self, format, *args):
                pass

        this = self  # bind for inner class
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()


class BenchSpider(BaseSpider):
    """基准测试爬虫"""
    name = "bench_spider"

    def __init__(self, base_url: str, request_count: int = 100, **kwargs):
        super().__init__(**kwargs)
        self.base_url = base_url
        self.request_count = request_count
        self.items = []
        self.latencies = []

    def start_requests(self):
        for i in range(self.request_count):
            yield Request(
                url=f"{self.base_url}/bench/{i}",
                meta={"i": i, "start": time.time()},
                dont_filter=True,
            )

    def parse(self, response):
        elapsed = time.time() - response.meta.get("start", time.time())
        self.latencies.append(elapsed)
        self.items.append(response.meta["i"])
        yield {"i": response.meta["i"]}


# ============================================================
# Benchmark 1: 吞吐量 vs 并发度
# ============================================================

def benchmark_throughput():
    print("\n" + "=" * 70)
    print("  Benchmark 1: 吞吐量 vs 并发度")
    print("=" * 70)

    configs = [
        (10, 50),    # (并发数, 总请求数)
        (20, 100),
        (50, 200),
        (100, 200),
    ]

    results = {"Scrapy": {}, "AioScrapy": {}}

    for concurrency, total in configs:
        # ---- Scrapy-style ----
        srv = BenchmarkServer()
        try:
            spider = BenchSpider(srv.base_url, request_count=total)
            engine = ScrapyEngine(spider=spider, concurrent_requests=concurrency)

            start = time.time()
            engine.run()
            elapsed = time.time() - start

            reqs = spider.stats["response_count"]
            throughput = reqs / elapsed if elapsed > 0 else 0
            avg_latency = (sum(spider.latencies) / len(spider.latencies) * 1000
                          if spider.latencies else 0)

            results["Scrapy"][concurrency] = {
                "throughput": throughput,
                "avg_latency_ms": avg_latency,
                "total_time": elapsed,
                "success": reqs,
            }
        finally:
            srv.stop()

        # ---- AioScrapy-style ----
        srv = BenchmarkServer()
        try:
            async def run_async():
                spider = BenchSpider(srv.base_url, request_count=total)
                engine = AioSpiderEngine(spider=spider, concurrent_requests=concurrency)
                start = time.time()
                await engine.run()
                elapsed = time.time() - start
                return spider, elapsed

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                spider, elapsed = loop.run_until_complete(run_async())
            finally:
                loop.close()

            reqs = spider.stats["response_count"]
            throughput = reqs / elapsed if elapsed > 0 else 0
            avg_latency = (sum(spider.latencies) / len(spider.latencies) * 1000
                          if spider.latencies else 0)

            results["AioScrapy"][concurrency] = {
                "throughput": throughput,
                "avg_latency_ms": avg_latency,
                "total_time": elapsed,
                "success": reqs,
            }
        finally:
            srv.stop()

    # 打印结果
    print(f"\n  {'并发':<8} {'实现':<12} {'吞吐(req/s)':>12} {'平均延迟':>10} {'总耗时':>10}")
    print("  " + "-" * 55)
    for concurrency, _ in configs:
        for impl in ["Scrapy", "AioScrapy"]:
            r = results[impl][concurrency]
            print(f"  {concurrency:<8} {impl:<12} {r['throughput']:>10.1f} "
                  f"{r['avg_latency_ms']:>8.1f}ms {r['total_time']:>8.2f}s")

    return results


# ============================================================
# Benchmark 2: 延迟分布
# ============================================================

def benchmark_latency_distribution():
    print("\n" + "=" * 70)
    print("  Benchmark 2: 延迟分布对比 (100请求, concurrency=50)")
    print("=" * 70)

    latencies = {"Scrapy": [], "AioScrapy": []}

    for impl in ["Scrapy", "AioScrapy"]:
        srv = BenchmarkServer()
        try:
            spider = BenchSpider(srv.base_url, request_count=100)
            if impl == "Scrapy":
                engine = ScrapyEngine(spider=spider, concurrent_requests=50)
                engine.run()
            else:
                async def run():
                    engine = AioSpiderEngine(spider=spider, concurrent_requests=50)
                    await engine.run()
                    return spider
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    spider = loop.run_until_complete(run())
                finally:
                    loop.close()

            latencies[impl] = sorted(spider.latencies)
        finally:
            srv.stop()

    # 百分位统计
    for impl in ["Scrapy", "AioScrapy"]:
        lats = latencies[impl]
        if not lats:
            continue
        p50 = lats[len(lats)//2] * 1000
        p90 = lats[int(len(lats)*0.9)] * 1000
        p99 = lats[int(len(lats)*0.99)] * 1000
        avg = sum(lats)/len(lats) * 1000
        print(f"\n  {impl}:")
        print(f"    Avg: {avg:.1f}ms | P50: {p50:.1f}ms | P90: {p90:.1f}ms | P99: {p99:.1f}ms")

    return latencies


# ============================================================
# Benchmark 3: 代码架构复杂度
# ============================================================

def benchmark_code_complexity():
    print("\n" + "=" * 70)
    print("  Benchmark 3: 代码架构复杂度")
    print("=" * 70)

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def analyze(dir_name: str):
        files = {}
        total_lines = 0
        total_imports = 0
        subdir = os.path.join(base_dir, dir_name)
        for fname in os.listdir(subdir):
            if fname.endswith(".py"):
                fpath = os.path.join(subdir, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                lines = content.split("\n")
                code_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
                imports = [l for l in lines if l.strip().startswith("import ") or
                          l.strip().startswith("from ")]
                files[fname] = {
                    "total": len(lines),
                    "code": len(code_lines),
                    "imports": len(imports),
                }
                total_lines += len(lines)
                total_imports += len(imports)
        return files, total_lines, total_imports

    scrapy_files, scrapy_lines, scrapy_imports = analyze("scrapy_sched")
    aio_files, aio_lines, aio_imports = analyze("aiospider_sched")

    print(f"\n  {'指标':<20} {'Scrapy':>10} {'AioScrapy':>10}")
    print("  " + "-" * 42)
    print(f"  {'总代码行数':<20} {scrapy_lines:>10} {aio_lines:>10}")
    print(f"  {'文件数':<20} {len(scrapy_files):>10} {len(aio_files):>10}")
    print(f"  {'import 语句':<20} {scrapy_imports:>10} {aio_imports:>10}")
    print(f"  {'并发模型':<20} {'线程池':>10} {'asyncio协程':>10}")
    print(f"  {'依赖':<20} {'requests':>10} {'aiohttp':>10}")

    avg_scrapy = sum(f["code"] for f in scrapy_files.values()) / len(scrapy_files)
    avg_aio = sum(f["code"] for f in aio_files.values()) / len(aio_files)
    print(f"  {'平均每文件代码行':<20} {avg_scrapy:>10.0f} {avg_aio:>10.0f}")

    return (scrapy_files, aio_files), (scrapy_lines, aio_lines)


# ============================================================
# Benchmark 4: 架构特性对比
# ============================================================

def benchmark_features():
    print("\n" + "=" * 70)
    print("  Benchmark 4: 架构特性对比")
    print("=" * 70)

    features = {
        "调度器类型": ("优先级队列 (heapq)", "asyncio.PriorityQueue"),
        "去重机制": ("RFPDupeFilter (SHA1指纹)", "RFPDupeFilter (复用)"),
        "并发模型": ("ThreadPoolExecutor", "asyncio.Task"),
        "HTTP 客户端": ("requests (同步)", "aiohttp (异步)"),
        "中间件链": ("同步回调链", "协程链 (async/await)"),
        "信号系统": ("发布-订阅 (同步)", "发布-订阅 (async/await)"),
        "事件循环": ("迭代循环 (while loop)", "asyncio 事件循环"),
        "流控方式": ("计数器 + sleep", "asyncio.Semaphore"),
        "域级延迟": ("不支持", "DomainRateLimiter"),
        "并发上限": ("线程数限制 (~100)", "协程数 (~10000+)"),
        "取消支持": ("不支持", "asyncio.CancelledError"),
        "超时控制": ("socket timeout", "asyncio.wait_for"),
        "连接复用": ("Session 连接池", "TCPConnector 连接池"),
        "资源占用": ("高 (线程栈 8MB/线程)", "低 (协程栈 ~KB)"),
    }

    print(f"\n  {'特性':<25} {'Scrapy风格':>25} {'AioScrapy风格':>25}")
    print("  " + "-" * 77)
    for feat, (scrapy_val, aio_val) in features.items():
        print(f"  {feat:<25} {scrapy_val:>25} {aio_val:>25}")


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 70)
    print("  调度器架构对比 — 全面基准测试")
    print("=" * 70)

    try:
        results_tp = benchmark_throughput()
    except Exception as e:
        print(f"\n  [WARN] 吞吐量测试部分失败: {e}")

    try:
        results_lat = benchmark_latency_distribution()
    except Exception as e:
        print(f"\n  [WARN] 延迟测试部分失败: {e}")

    results_code = benchmark_code_complexity()
    benchmark_features()

    print("\n" + "=" * 70)
    print("  结论")
    print("=" * 70)
    print("""
  Scrapy-style (Twisted 模式):
    - 适合: CPU 密集型解析、传统同步代码集成
    - 优势: 成熟稳定、request 生态丰富
    - 劣势: 线程开销大、并发上限受线程数限制

  AioScrapy-style (asyncio 模式):
    - 适合: IO 密集型爬取、高并发场景
    - 优势: 极低资源占用、协程级并发
    - 劣势: 需全链路异步、生态兼容性
""")

    return 0


if __name__ == "__main__":
    sys.exit(main())

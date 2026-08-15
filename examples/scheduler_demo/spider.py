"""
scheduler_demo —— jimmyspider.scheduler 调度引擎演示（AioSpiderEngine）

演示爬虫架构五层模型：Engine → Scheduler → Downloader → Middleware → Spider。
爬虫逻辑与 Scrapy 完全同构：start_requests 产出起始请求，
parse 产出「新 Request（继续爬）」和「dict（数据项）」，process_item 保存。

默认离线运行（--mode offline，内置 mock HTML，不联网，可离线验证）；
加 --mode online 可抓真实 Hacker News（https://news.ycombinator.com/）。

用法:
  python spider.py                         # 离线演示（默认, AioSpiderEngine）
  python spider.py --engine scrapy         # 用 ScrapyEngine（同步线程池）
  python spider.py --engine both           # 两种引擎对比
  python spider.py --mode online           # 抓真实 Hacker News
"""

import argparse
import asyncio
import sqlite3
import sys
import time

# Windows GBK 控制台无法打印中文/emoji，统一切到 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from bs4 import BeautifulSoup
from jimmyspider.scheduler import (
    AioSpiderEngine, BaseSpider, Request, Response, ScrapyEngine,
)
from jimmyspider.scheduler.common.middleware import DownloaderMiddleware

HN_LIST_URL = "https://news.ycombinator.com/"

# ----------------------------------------------------------------------
# 内置 mock 数据（离线演示用，结构与真实 Hacker News 一致）
# ----------------------------------------------------------------------

MOCK_LIST_HTML = """<html><head><title>Hacker News (mock)</title></head><body>
<table class="itemlist">
  <tr class="athing"><td class="title"><span class="titleline">
    <a href="https://news.example.com/story/1">Rust 1.90 发布（mock 数据）</a></span></td></tr>
  <tr class="athing"><td class="title"><span class="titleline">
    <a href="https://news.example.com/story/2">Python 3.14 性能提升一览（mock 数据）</a></span></td></tr>
  <tr class="athing"><td class="title"><span class="titleline">
    <a href="https://news.example.com/story/3">asyncio 并发爬虫实践（mock 数据）</a></span></td></tr>
  <tr class="athing"><td class="title"><span class="titleline">
    <a href="https://news.example.com/story/4">如何设计消息队列流水线（mock 数据）</a></span></td></tr>
  <tr class="athing"><td class="title"><span class="titleline">
    <a href="https://news.example.com/story/5">jimmySpider 调度器解析（mock 数据）</a></span></td></tr>
  <tr class="athing"><td class="title"><span class="titleline">
    <a href="https://news.example.com/story/6">终端里的天气 CLI 工具（mock 数据）</a></span></td></tr>
</table>
</body></html>"""

def mock_html_for(url: str) -> str:
    """为 URL 生成 mock 响应：列表页返回列表，详情页按编号生成"""
    if url == HN_LIST_URL:
        return MOCK_LIST_HTML
    if url.startswith("https://news.example.com/story/"):
        n = url.rsplit("/", 1)[-1]
        return f"""<html><head><title>HN Story {n} (mock)</title></head><body>
<table><tr class="athing"><td class="title"><span class="titleline">
  <a href="{url}">Mock Story {n}</a></span></td></tr>
<tr><td class="subtext"><span class="score">{42 + int(n) * 7} points</span></td></tr></table>
</body></html>"""
    return None

# ----------------------------------------------------------------------
# 下载器中间件：离线模式下用 mock HTML 短路真实网络请求
# ----------------------------------------------------------------------

class MockDownloaderMiddleware(DownloaderMiddleware):
    """
    process_request 返回 Response 时短路下载（无需联网）。
    在线模式（mock=False）返回 None，走真实 aiohttp/requests 下载。
    """

    def __init__(self, mock: bool = True):
        self.mock = mock

    def process_request(self, request: Request):
        if not self.mock:
            return None
        html = mock_html_for(request.url)
        if html is not None:
            return Response(
                url=request.url, status=200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                body=html.encode("utf-8"), request=request,
            )
        return None

# ----------------------------------------------------------------------
# 爬虫：与 Scrapy 写法完全一致，两种引擎通用
# ----------------------------------------------------------------------

class HackerNewsSpider(BaseSpider):
    """抓 Hacker News 列表页 → 前 N 条新闻的详情页 → 入库 SQLite"""

    name = "hn_demo"

    def __init__(self, limit: int = 5, db_path: str = "results.db", **kwargs):
        super().__init__(**kwargs)
        self.limit = limit
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("DROP TABLE IF EXISTS items")
            conn.execute("CREATE TABLE items (title TEXT, url TEXT, points INT, rank INT)")
            conn.commit()
        finally:
            conn.close()

    # ---- 入口：起始请求（列表页） ----
    def start_requests(self):
        yield Request(url=HN_LIST_URL, callback="parse_list", dont_filter=True)

    # ---- 列表页解析：产出详情页 Request（带 meta 传递数据 + 指定回调） ----
    def parse_list(self, response: Response):
        soup = BeautifulSoup(response.text, "html.parser")
        for rank, a in enumerate(soup.select("span.titleline > a")[:self.limit], start=1):
            yield Request(
                url=a.get("href"),
                callback="parse_detail",            # 指定回调函数
                meta={"rank": rank, "title": a.get_text(strip=True)},
            )

    # ---- 详情页解析：产出 dict → 引擎交给 process_item ----
    def parse_detail(self, response: Response):
        soup = BeautifulSoup(response.text, "html.parser")
        score_el = soup.select_one("span.score")
        points = int(score_el.get_text().split()[0]) if score_el else 0
        yield {
            "title": response.meta.get("title", ""),
            "url": response.url,
            "points": points,
            "rank": response.meta.get("rank", 0),
        }

    # ---- 数据保存 ----
    def process_item(self, item: dict):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO items (title, url, points, rank) VALUES (?, ?, ?, ?)",
                (item["title"], item["url"], item["points"], item["rank"]),
            )
            conn.commit()
        finally:
            conn.close()
        print(f"  [Item] #{item['rank']} {item['title']} "
              f"(+{item['points']} points) -> {item['url']}")
        return super().process_item(item)   # 基类统计 item_count

# ----------------------------------------------------------------------
# 引擎运行（Aio = asyncio 协程；Scrapy = 同步回调 + 线程池）
# ----------------------------------------------------------------------

async def run_aio(spider: HackerNewsSpider, mock: bool, concurrent: int) -> dict:
    t0 = time.time()
    engine = AioSpiderEngine(
        spider=spider,
        downloader_middlewares=[MockDownloaderMiddleware(mock)],
        concurrent_requests=concurrent,
        max_requests=1 + spider.limit,      # 列表页 + limit 个详情页
    )
    await engine.run()
    return {"elapsed": time.time() - t0, "summary": spider.summary()}

def run_scrapy(spider: HackerNewsSpider, mock: bool, concurrent: int) -> dict:
    t0 = time.time()
    engine = ScrapyEngine(
        spider=spider,
        downloader_middlewares=[MockDownloaderMiddleware(mock)],
        concurrent_requests=concurrent,
    )
    engine.run()
    return {"elapsed": time.time() - t0, "summary": spider.summary()}

def print_db_rows(db_path: str = "results.db"):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT rank, title, points FROM items ORDER BY rank").fetchall()
    conn.close()
    print(f"\n[DB] SQLite results.db 共 {len(rows)} 条记录：")
    for rank, title, points in rows:
        print(f"  #{rank} {title} (+{points} points)")


def main():
    parser = argparse.ArgumentParser(description="jimmySpider 调度引擎演示")
    parser.add_argument("--engine", choices=["aio", "scrapy", "both"], default="aio",
                        help="aio=AioSpiderEngine(asyncio, 默认)；scrapy=ScrapyEngine；both=对比")
    parser.add_argument("--mode", choices=["offline", "online"], default="offline",
                        help="offline=内置 mock HTML 离线演示(默认)；online=抓真实 Hacker News")
    parser.add_argument("--limit", type=int, default=5, help="抓取的详情页数量")
    parser.add_argument("--concurrent", type=int, default=10, help="并发请求数")
    args = parser.parse_args()

    print("=" * 60)
    print(f"scheduler_demo —— 引擎: {args.engine} | 模式: {args.mode} | "
          f"详情页: {args.limit} | 并发: {args.concurrent}")
    print("=" * 60)

    mock = args.mode == "offline"
    if not mock:
        print("[*] 在线模式：将请求 https://news.ycombinator.com/ （需联网）\n")

    if args.engine == "aio":
        asyncio.run(run_aio(HackerNewsSpider(limit=args.limit), mock, args.concurrent))
    elif args.engine == "scrapy":
        run_scrapy(HackerNewsSpider(limit=args.limit), mock, args.concurrent)
    else:
        # ---- 同一爬虫、同一并发、同一 mock 数据下的引擎对比 ----
        aio = asyncio.run(run_aio(HackerNewsSpider(limit=args.limit), mock, args.concurrent))
        scrapy = run_scrapy(HackerNewsSpider(limit=args.limit), mock, args.concurrent)
        print("\n" + "=" * 60)
        print(f"[对比] AioSpiderEngine : {aio['elapsed']:.3f}s")
        print(f"[对比] ScrapyEngine    : {scrapy['elapsed']:.3f}s")
        print(f"[对比] 输出: {aio['summary']}")
        print(f"[对比] 输出: {scrapy['summary']}")
        print("=" * 60)

    print_db_rows()


if __name__ == "__main__":
    main()

"""
clash_proxy_pool — Clash 节点自动切换示例爬虫

演示：
1. ClashManager 自动健康检测 + 节点轮换
2. 每次下载达到上限 (max_downloads_per_node) 自动切换节点
3. 403 错误自动触发切换
4. 爬虫通过 Clash 代理出口 (127.0.0.1:7777) 请求

运行前提:
    1. docker compose up -d  （启动 Clash 容器）
    2. config/config.yaml 使用真实订阅配置
    3. .env 中配置 CLASH_SECRET
"""
import os
from pathlib import Path

from jimmyspider import JimmySpider, Cache
from jimmyspider.proxy_clash import ClashManager


class Spider(JimmySpider):
    def __init__(self, **kwargs):
        kwargs.setdefault("test_url", "https://www.google.com/")
        super().__init__(**kwargs)

        # ---- ClashManager：自动切换节点 ----
        self.clash = ClashManager({
            "api_url": os.environ.get("CLASH_API_URL", "http://127.0.0.1:9099"),
            "secret": os.environ.get("CLASH_SECRET", "your-secret-here"),
            "group_name": os.environ.get("CLASH_GROUP_NAME", "🚀节点选择"),
            "proxy_port": int(os.environ.get("CLASH_PROXY_PORT", "7777")),
            "post_switch_test_url": "https://www.google.com/",
            "enable_node_switching": True,
            "max_downloads_per_node": 100,       # 每个节点最多下载 100 次
            "max_403_errors": 3,                 # 连续 3 次 403 触发切换
            "switch_strategy": "round_robin",    # 轮询切换
            "require_post_switch_connectivity": True,  # 切换后必须验证出口
        })

        # 启动后台健康检测（每 30 秒对所有节点测延迟）
        self.clash.start_auto_health_check(interval_sec=30)

        self.page = Cache(f"{self.table_name}_page")

    def run(self):
        page = self.page.get_int(default=1)

        while True:
            # 请求前检查节点健康，不健康自动切换
            if not self.clash.get_healthy_nodes():
                self.log_print.warning("无健康节点，等待恢复...")
                self.clash.switch_to_healthy_node()
                continue

            url = f"https://example.com/api?page={page}"
            self.log_print.info(
                f"抓取第 {page} 页 | 当前节点: "
                f"{self.clash.get_current_node()['name'] if self.clash.get_current_node() else '?'}"
            )

            # 通过 Clash 代理出口请求
            res = self.single_fetcher.fetch(
                url,
                proxies=self.clash.get_proxy_config(),  # {"http": "127.0.0.1:7777", ...}
            )
            if not res:
                # 失败：可能节点挂了，触发切换
                self.log_print.warning(f"第 {page} 页失败，切换节点")
                self.clash.switch_node()
                continue

            data = res.get(url)
            if not data:
                break

            # 记录下载次数，达到上限自动切换
            self.clash.increment_download_count()

            self.save_result({"_id": str(page), "page": page, "data": data})
            self.page.record_int(page)
            page += 1

            # 演示只爬 5 页
            if page > 5:
                break


if __name__ == "__main__":
    Spider(pro_path=Path(__file__).parent).run()

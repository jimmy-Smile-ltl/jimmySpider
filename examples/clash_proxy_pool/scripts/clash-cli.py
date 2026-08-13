"""
Clash 代理池节点管理 CLI — 基于 jimmyspider.proxy_clash.ClashManager 的命令行工具。

用法:
  python clash-cli.py list               列出所有节点及健康状态
  python clash-cli.py switch             轮询切换到下一节点
  python clash-cli.py switch --random    随机切换节点
  python clash-cli.py status             查看当前节点状态
  python clash-cli.py health             对所有节点测延迟
  python clash-cli.py test               测试 API + 代理出口连通性
  python clash-cli.py monitor            启动后台健康检查守护 (30s 间隔)

前置: 先 docker compose up -d 启动 Clash 容器
"""

import argparse
import os
import sys

from jimmyspider.proxy_clash import ClashManager


def get_config():
    secret = os.environ.get("CLASH_SECRET", "your-secret-here")
    return {
        "api_url": os.environ.get("CLASH_API_URL", "http://127.0.0.1:9099"),
        "secret": secret,
        "post_switch_test_url": os.environ.get(
            "CLASH_TEST_URL", "https://www.google.com/"
        ),
        "group_name": os.environ.get("CLASH_GROUP_NAME", "🚀节点选择"),
        "proxy_port": int(os.environ.get("CLASH_PROXY_PORT", "7777")),
        "enable_node_switching": True,
        "max_downloads_per_node": 500,
        "switch_strategy": "round_robin",
        "show_node_info": True,
        "pre_switch_delay_check": True,
        "require_post_switch_connectivity": True,
        "only_use_verified_nodes": True,
    }


def cmd_list(manager: ClashManager):
    manager.refresh_nodes()
    manager.refresh_node_health()
    current = manager.get_current_node()
    current_name = current["name"] if current else "N/A"

    print(f"策略组: {manager.group_name}")
    print(f"当前节点: {current_name}")
    print(f"节点总数: {len(manager.available_nodes)}")
    print("-" * 60)

    healthy = manager.get_healthy_nodes()
    unhealthy = manager.get_unhealthy_nodes()
    print(f"健康节点: {len(healthy)} / 异常节点: {len(unhealthy)}")
    print()

    for node in manager.available_nodes:
        name = node.get("name", "?")
        is_current = "👉" if name == current_name else "  "
        health = "✅" if node.get("healthy") else "❌"
        delay = node.get("delay", "N/A")
        print(f"  {is_current} {health} {name}  (延迟: {delay})")


def cmd_switch(manager: ClashManager, random_mode: bool = False):
    if random_mode:
        manager.switch_strategy = "random"
    print(f"策略: {'随机' if random_mode else '轮询'}")
    ok = manager.switch_node()
    if ok:
        current = manager.get_current_node()
        print(f"当前节点: {current['name'] if current else '?'}")
    else:
        print("[❌] 切换失败")
        sys.exit(1)


def cmd_status(manager: ClashManager):
    current = manager.get_current_node()
    print(f"API:      {manager.api_url}")
    print(f"策略组:   {manager.group_name}")
    print(f"当前节点: {current['name'] if current else 'N/A'}")
    print(f"节点索引: {manager.current_node_index}")
    print(f"下载计数: {manager.download_count}")
    print(f"403 错误: {manager.error_403_count}/{manager.max_403_errors}")
    print(f"可用节点: {len(manager.available_nodes)}")


def cmd_health(manager: ClashManager):
    manager.refresh_nodes()
    manager.refresh_node_health()
    print(f"测延迟中... (策略组: {manager.group_name})")
    print("-" * 50)
    for node in manager.available_nodes:
        name = node.get("name", "?")
        delay = node.get("delay", "N/A")
        healthy = "✅" if node.get("healthy") else "❌"
        print(f"  {healthy} {name}: {delay}ms")


def cmd_test(manager: ClashManager):
    print(f"测试 API 连通性 ({manager.api_url})...")
    if manager.test_connection():
        print("[✅] Clash API 可达")
    else:
        print("[❌] Clash API 不可达")
        sys.exit(1)

    print(f"测试代理出口 ({manager.proxy_port})...")
    if manager._post_switch_connectivity_ok():
        print(f"[✅] 代理出口可用 → {manager.post_switch_test_url}")
    else:
        print(f"[❌] 代理出口不可用 → {manager.post_switch_test_url}")
        sys.exit(1)


def cmd_monitor(manager: ClashManager, interval: int = 30):
    print(f"启动健康检查守护 (间隔 {interval}s)...")
    try:
        manager.start_auto_health_check(interval)
    except KeyboardInterrupt:
        print("\n已停止")


def main():
    parser = argparse.ArgumentParser(description="Clash 代理池节点管理")
    sub = parser.add_subparsers(dest="command", help="子命令")

    sub.add_parser("list", help="列出所有节点")
    sp = sub.add_parser("switch", help="切换节点")
    sp.add_argument("--random", action="store_true", help="随机切换")
    sub.add_parser("status", help="查看当前状态")
    sub.add_parser("health", help="对所有节点测延迟")
    sub.add_parser("test", help="测试代理连通性")
    mp = sub.add_parser("monitor", help="后台健康检查")
    mp.add_argument("--interval", type=int, default=30, help="检查间隔秒数")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = get_config()
    manager = ClashManager(config)

    if args.command == "list":
        cmd_list(manager)
    elif args.command == "switch":
        cmd_switch(manager, random_mode=args.random)
    elif args.command == "status":
        cmd_status(manager)
    elif args.command == "health":
        cmd_health(manager)
    elif args.command == "test":
        cmd_test(manager)
    elif args.command == "monitor":
        cmd_monitor(manager, interval=args.interval)


if __name__ == "__main__":
    main()

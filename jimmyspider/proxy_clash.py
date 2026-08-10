import datetime
import requests
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from typing import List, Dict, Optional
from jimmyspider.cache import Cache
from jimmyspider.config import get_config
from urllib.parse import urljoin, urlparse, parse_qs
class ClashManager:
    """Clash代理管理器"""

    def __init__(self, config: Dict = None):
        if config is None:
            config = {}
        cfg = get_config()
        self.config = config
        self.api_url = config.get("api_url", cfg.CLASH_API_URL)
        self.secret = config.get("secret", cfg.CLASH_SECRET)  # API密码
        self.policy_group = config.get("policy_group", cfg.CLASH_POLICY_GROUP)
        # 策略组名称，兼容旧配置键 group_name（后续代码统一使用 self.group_name）
        self.group_name = config.get("group_name", self.policy_group)  # 策略组名    称
        self.proxy_port = config.get("proxy_port", 7897)
        self.enable_node_switching = config.get("enable_node_switching", True)
        self.max_downloads_per_node = config.get("max_downloads_per_node", 500)
        self.switch_strategy = config.get("switch_strategy", "round_robin")
        self.show_node_info = config.get("show_node_info", True)
        # 切换后连通性检测配置
        if not config.get("post_switch_test_url", ""):
            raise ValueError("配置错误: post_switch_test_url 参数不能为空 必须提供一个用于切换后连通性检测的URL")
        self.post_switch_test_url = config.get("post_switch_test_url", "https://www.gstatic.com/generate_204")
        self.post_switch_timeout_sec = config.get("post_switch_timeout_sec", 5)
        self.post_switch_max_retries = config.get("post_switch_max_retries", 3)
        # 切换前：用 Clash API 对候选节点测延迟，跳过明显不可用的节点（避免白等 set_node + sleep）
        self.pre_switch_delay_check = config.get("pre_switch_delay_check", True)
        # 切换后：除 API 验证「当前选中名」外，必须经本地代理出口能访问外网
        self.require_post_switch_connectivity = config.get("require_post_switch_connectivity", True)

        # 节点状态文件
        self.state_file = "clash_node_state.json"

        # 连通性已验证的节点池（只在这些节点里轮换）
        self.only_use_verified_nodes = config.get("only_use_verified_nodes", True)
        self.verified_nodes: set = set()  # 存 node name

        # 初始化状态
        self.current_node_index = 0
        self.download_count = 0
        self.node_download_counts = {}
        self.available_nodes = []
        self.error_403_count = 0  # 403错误计数
        self.max_403_errors = 3  # 最大403错误数
        self._switch_condition = threading.Condition()
        self._is_switching = False
        # 保存的是被封锁ip 共有的  网站1 封锁这个ip 网站2就算能请求 也不给请求 这样不好
        # 一个网站一个？ 好像可以 加上  post_switch_test_url 就是说这个参数必须有
        web_loc = urlparse(self.post_switch_test_url).netloc.replace(".","_")
        if not web_loc:
            raise  ValueError(f" {self.post_switch_test_url} 提取 web_loc {web_loc} 异常")
        self.error_node_set = Cache(f"clash_error_node_set_{web_loc}")
        print(f" {self.post_switch_test_url}  挂掉的节点set 保存在 redis clash_error_node_set_{web_loc}")
        # 加载保存的状态
        self.load_state()
        self.healthy_node_idx = -1

        # 初始化时获取可用节点，并让 Clash 策略组与 current_node_index 一致
        if self.enable_node_switching:
            if self.refresh_nodes():
                self.refresh_node_health()

    def _disable_proxy_env(self):
        """临时禁用代理环境变量"""
        proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']
        original_values = {}
        for var in proxy_vars:
            if var in os.environ:
                original_values[var] = os.environ[var]
                del os.environ[var]
        return original_values

    def _restore_proxy_env(self, original_values):
        """恢复代理环境变量"""
        for var, value in original_values.items():
            os.environ[var] = value

    def _find_available_groups(self) -> List[str]:
        """查找可用的策略组"""
        try:
            headers = {}
            if self.secret:
                headers['Authorization'] = f'Bearer {self.secret}'

            original_proxies = self._disable_proxy_env()
            try:
                response = requests.get(f"{self.api_url}/proxies", headers=headers, timeout=10,
                                        proxies={"http": None, "https": None})
            finally:
                self._restore_proxy_env(original_proxies)

            if response.status_code == 200:
                data = response.json()
                selector_groups = []
                for name, info in data.get('proxies', {}).items():
                    if isinstance(info, dict) and info.get('type') == 'Selector':
                        selector_groups.append(name)
                return selector_groups
        except Exception as e:
            print(f"[⚠️] 查找策略组失败: {e}")
        return []

    def refresh_nodes(self) -> bool:
        """刷新可用节点列表"""
        try:
            # 准备请求头
            error_nodes = self.error_node_set.get_set_members()
            headers = {}
            if self.secret:
                headers['Authorization'] = f'Bearer {self.secret}'
            # 临时禁用代理环境变量，避免全局代理导致的502错误
            original_proxies = self._disable_proxy_env()

            try:
                # 获取策略组信息 - 强制使用直连
                response = requests.get(f"{self.api_url}/proxies/{self.group_name}", headers=headers, timeout=10,
                                        proxies={"http": None, "https": None})
            finally:
                # 恢复代理环境变量
                self._restore_proxy_env(original_proxies)

            # 如果策略组不存在（404），尝试查找可用的策略组
            if response.status_code == 404:
                print(f"[⚠️] 策略组 '{self.group_name}' 不存在，查找可用的策略组...")
                available_groups = self._find_available_groups()
                if available_groups:
                    print(f"[🔍] 找到 {len(available_groups)} 个可用策略组:")
                    for i, group in enumerate(available_groups[:10], 1):  # 只显示前10个
                        print(f"    {i}. {group}")
                    if len(available_groups) > 10:
                        print(f"    ... 还有 {len(available_groups) - 10} 个")

                    # 使用第一个找到的策略组
                    self.group_name = available_groups[0]
                    print(f"[✅] 自动切换到策略组: {self.group_name}")

                    # 重新请求
                    original_proxies = self._disable_proxy_env()
                    try:
                        response = requests.get(f"{self.api_url}/proxies/{self.group_name}", headers=headers,
                                                timeout=10, proxies={"http": None, "https": None})
                    finally:
                        self._restore_proxy_env(original_proxies)
                else:
                    print(f"[❌] 未找到任何可用的策略组")
                    return False

            if response.status_code == 200:
                data = response.json()

                # 添加调试信息
                print(f"[🔍] 从策略组 '{self.group_name}' 获取节点信息")

                # 解析策略组节点信息
                self.available_nodes = []

                # 获取策略组的所有节点
                all_nodes = data.get("all", [])

                # 先获取所有代理信息，用于过滤策略组
                all_proxies = {}
                try:
                    proxies_response = requests.get(f"{self.api_url}/proxies", headers=headers, timeout=10,
                                                    proxies={"http": None, "https": None})
                    if proxies_response.status_code == 200:
                        proxies_data = proxies_response.json()
                        all_proxies = proxies_data.get("proxies", {})
                except Exception as e:
                    print(f"[⚠️] 获取所有代理信息失败: {e}")

                # 处理两种可能的数据结构（排除订阅元数据）
                # 不要用单独的「流量」：节点名常含「流量倍率」，误杀几乎全部线路
                invalid_patterns = ("最新网址", "剩余流量", "过期时间", "订阅", "到期")
                if all_nodes:
                    if abs(len(error_nodes) - len(all_nodes))  < 5:
                        print(f"[⚠️] 注意：当前策略组节点数量 {len(all_nodes)} 与错误节点数量 {len(error_nodes)} 接近，可能误杀过多节点，清空与错误节点缓存 ")
                        self.error_node_set.clear_value()
                        error_nodes = []
                    if isinstance(all_nodes[0], dict):
                        # 结构1: [{"name": "xxx", "type": "xxx"}, ...]
                        for node_info in all_nodes:
                            if isinstance(node_info, dict):
                                node_name = node_info.get("name")
                                node_type = node_info.get("type", "Unknown")
                                if node_name and any(p in str(node_name) for p in invalid_patterns):
                                    continue
                            else:
                                continue

                            # 检查是否是策略组（Selector类型），如果是则跳过
                            if node_name in all_proxies:
                                proxy_info = all_proxies[node_name]
                                if isinstance(proxy_info, dict) and proxy_info.get("type") == "Selector":
                                    # print(f"[⏭️] 跳过策略组: {node_name}")
                                    continue

                            # 只添加实际的代理节点（排除策略组）
                            # 允许的类型：Shadowsocks, Vmess, Trojan, Socks5, Vless, Http, Snell等
                            allowed_types = ["Shadowsocks", "Vmess", "Trojan", "Socks5", "Vless", "Http", "Snell",
                                             "Wireguard"]
                            if node_type not in allowed_types and node_type != "Unknown":
                                # 如果类型不在允许列表中，检查是否是策略组
                                if node_type == "Selector":
                                    print(f"[⏭️] 跳过策略组: {node_name}")
                                    continue
                            # 没有测延迟就不管了
                            if node_name not in error_nodes and node_info.get("delay",True):
                                self.available_nodes.append({
                                    "name": node_name,
                                    "type": node_type,
                                    "delay": 0,
                                    "healthy": True,
                                    "last_check": time.time()
                                })
                            # print(f"[✅] 添加节点: {node_name} ({node_type})")
                    else:
                        # 结构2: ["节点1", "节点2", ...]
                        for node_name in all_nodes:
                            if isinstance(node_name, str):
                                # 排除订阅元数据（非真实代理节点）
                                if any(p in node_name for p in invalid_patterns):
                                    continue
                                # 检查是否是策略组
                                if node_name in all_proxies:
                                    proxy_info = all_proxies[node_name]
                                    if isinstance(proxy_info, dict) and proxy_info.get("type") == "Selector":
                                        print(f"[⏭️] 跳过策略组: {node_name}")
                                        continue

                                # 尝试获取节点类型
                                node_type = "Unknown"
                                if node_name in all_proxies:
                                    proxy_info = all_proxies[node_name]
                                    if isinstance(proxy_info, dict):
                                        node_type = proxy_info.get("type", "Unknown")

                                # 只添加实际的代理节点
                                allowed_types = ["Shadowsocks", "Vmess", "Trojan", "Socks5", "Vless", "Http", "Snell",
                                                 "Wireguard"]
                                if node_type not in allowed_types and node_type != "Unknown":
                                    if node_type == "Selector":
                                        print(f"[⏭️] 跳过策略组: {node_name}")
                                        continue
                                if node_name not in error_nodes:
                                    self.available_nodes.append({
                                        "name": node_name,
                                        "type": node_type,
                                        "delay": 0,
                                        "healthy": True,
                                        "last_check": time.time()
                                    })
                                # print(f"[✅] 添加节点: {node_name} ({node_type})")

                # print(f"[📊] 找到 {len(self.available_nodes)} 个符合条件的节点")

                # 验证并修正节点索引
                self._validate_node_index()

                # 显示节点基本信息（不检测健康状态）
                if self.show_node_info:
                    for i, node in enumerate(self.available_nodes):
                        print(f"    {i + 1}. {node['name']} ({node['type']}) - 延迟: 未检测")

                return True
            else:
                print(f"[⚠️] 获取节点列表失败: {response.status_code}")
                if response.status_code == 401:
                    print(f"[💡] 提示: 可能需要设置正确的API密钥 (secret)")
                elif response.status_code == 404:
                    print(f"[💡] 提示: 策略组 '{self.group_name}' 不存在，请检查Clash配置")
                return False

        except Exception as e:
            print(f"[‼] 连接Clash API失败: {e}")
            return False

    def load_state(self):
        """加载保存的状态"""
        try:
            if os.path.exists(self.state_file):
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.current_node_index = state.get('current_node_index', 0)
                    self.download_count = state.get('download_count', 0)
                    self.node_download_counts = state.get('node_download_counts', {})
                    self.verified_nodes = set(state.get('verified_nodes', []))
                    print(f"[📂] 加载保存的节点状态: 索引={self.current_node_index}, 下载计数={self.download_count}, 已验证节点={len(self.verified_nodes)}")
            else:
                print("[📂] 未找到保存的节点状态文件，使用默认值")
        except Exception as e:
            print(f"[⚠️] 加载节点状态失败: {e}")

    def _validate_node_index(self):
        """验证并修正节点索引"""
        if self.available_nodes and self.current_node_index >= len(self.available_nodes):
            print(f"[⚠️] 当前节点索引 {self.current_node_index} 超出范围，重置为0")
            self.current_node_index = 0

    def _ensure_clash_matches_saved_index(self) -> None:
        """
        启动时同步：Clash 策略组「当前选中」(now) 可能与本地索引不一致
        （例如换订阅/新代理后 UI 仍停留在旧节点）。此处按 current_node_index PUT 一次。
        """
        node = self.get_current_node()
        if not node:
            return
        try:
            headers = {}
            if self.secret:
                headers["Authorization"] = f"Bearer {self.secret}"
            original_proxies = self._disable_proxy_env()
            try:
                response = requests.get(
                    f"{self.api_url}/proxies/{self.group_name}",
                    headers=headers,
                    timeout=10,
                    proxies={"http": None, "https": None},
                )
            finally:
                self._restore_proxy_env(original_proxies)
            if response.status_code != 200:
                return
            now = (response.json() or {}).get("now", "")
            target = node["name"]
            if now == target:
                print(f"[✅] 策略组 '{self.group_name}' 已选中: {target}")
                return
            print(
                f"[🔄] 策略组当前为「{now}」，与本地索引节点「{target}」不一致，正在同步..."
            )
            if self.set_node_by_name(target):
                time.sleep(1.5)
                if self.verify_node_switch(target):
                    print(f"[✅] 已同步到节点: {target}")
                else:
                    print(
                        f"[⚠️] 已请求切换至「{target}」但验证未通过，请检查 Clash 或节点名称是否仍有效"
                    )
        except Exception as e:
            print(f"[⚠️] 同步策略组选中节点失败: {e}")

    def save_state(self):
        """保存当前状态"""
        try:
            state = {
                'current_node_index': self.current_node_index,
                'download_count': self.download_count,
                'node_download_counts': self.node_download_counts,
                'verified_nodes': list(self.verified_nodes),
                'timestamp': time.time()
            }
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            print(f"[💾] 节点状态已保存: 索引={self.current_node_index}, 下载计数={self.download_count}")
        except Exception as e:
            print(f"[⚠️] 保存节点状态失败: {e}")

    def _sort_nodes_by_region(self):
        """按地区分组排序节点"""
        # 定义地区优先级（数字越小优先级越高）
        region_priority = {
            "香港": 1,
            "日本": 2,
            "新加坡": 3,
            "台湾": 4,
            "美国": 5,
            "德国": 6,
            "荷兰": 7,
            "澳大利亚": 8,
            "英国": 9,
            "爱沙尼亚": 10
        }

        def get_region_priority(node_name):
            """获取节点地区优先级"""
            for region, priority in region_priority.items():
                if region in node_name:
                    return priority
            return 999  # 未知地区优先级最低

        # 按地区优先级排序
        self.available_nodes.sort(key=lambda x: get_region_priority(x["name"]))

    def _check_node_health_1(self):
        """检测节点健康状态"""
        # print("[🔍] 检测节点延迟和健康状态...")

        if not self.available_nodes:
            print("[⚠️] 没有可用节点进行健康检测")
            return

        for node in self.available_nodes:
            try:
                # print(f"[🔍] 检测节点: {node['name']}")
                # 测试节点延迟
                delay = self.get_node_delay(node["name"])
                # print(f"[🔍] 节点 {node['name']} 延迟测试结果: {delay}")

                if delay is not None and delay > 0:
                    node["delay"] = delay
                    node["healthy"] = True  # 有延迟表示节点健康
                    print(f"[✅] 节点 {node['name']} 健康，延迟: {delay}ms")
                else:
                    node["delay"] = 0
                    node["healthy"] = False  # 无法获取延迟表示节点不健康
                    print(f"[❌] 节点 {node['name']} 不健康，延迟: {delay}")

                node["last_check"] = time.time()

                # 添加延迟避免API请求过快
                time.sleep(0.1)

            except Exception as e:
                print(f"[⚠️] 检测节点 {node['name']} 健康状态失败: {e}")
                node["healthy"] = False
                node["delay"] = 0

        # 统计健康节点数量
        healthy_count = sum(1 for node in self.available_nodes if node.get("healthy", False))
        print(f"[📊] 健康节点: {healthy_count}/{len(self.available_nodes)}")

    def _check_node_health(self):
        """检测节点健康状态（多线程版本）"""
        if not self.available_nodes:
            print("[⚠️] 没有可用节点进行健康检测")
            return

        # 用于线程安全的打印（可选，避免输出混乱）
        print_lock = threading.Lock()

        def check_single_node(node):
            """检测单个节点的健康状态"""
            name = node["name"]
            try:
                # 测试节点延迟
                delay = self.get_node_delay(name)
                with print_lock:
                    print(f"[🔍] 节点 {name} 延迟测试结果: {delay}")

                if delay is not None and delay > 0:
                    node["delay"] = delay
                    node["healthy"] = True
                    with print_lock:
                        print(f"[✅] 节点 {name} 健康，延迟: {delay}ms")
                else:
                    node["delay"] = 0
                    node["healthy"] = False
                    with print_lock:
                        print(f"[❌] 节点 {name} 不健康，延迟: {delay}")

                node["last_check"] = time.time()

                # 可选：每个节点检测后短暂休眠，避免瞬间高频请求（可注释）
                # time.sleep(0.1)

            except Exception as e:
                with print_lock:
                    print(f"[⚠️] 检测节点 {name} 健康状态失败: {e}")
                node["healthy"] = False
                node["delay"] = 0

        # 使用线程池并发检测所有节点
        with ThreadPoolExecutor(max_workers=len(self.available_nodes)) as executor:
            futures = [executor.submit(check_single_node, node) for node in self.available_nodes]
            # 等待所有任务完成
            for future in as_completed(futures):
                # 捕获任务中的异常（可选）
                try:
                    future.result()
                except Exception as e:
                    print(f"[⚠️] 线程执行异常: {e}")

        # 统计健康节点数量
        healthy_count = sum(1 for node in self.available_nodes if node.get("healthy", False))
        print(f"[📊] 健康节点: {healthy_count}/{len(self.available_nodes)}")

    def _check_single_node_health(self, node: Dict) -> bool:
        """检测单个节点的健康状态"""
        try:
            # 如果最近已经检测过（5分钟内），直接返回缓存结果
            if node.get("last_check", 0) > time.time() - 300:  # 5分钟缓存
                return node.get("healthy", True)

            # print(f"[🔍] 检测节点延迟: {node['name']}")
            delay = self.get_node_delay(node["name"])

            if delay is not None and delay > 0:
                node["delay"] = delay
                node["healthy"] = True
                node["last_check"] = time.time()
                print(f"[✅] 节点 {node['name']} 健康，延迟: {delay}ms")
                return True
            else:
                node["delay"] = 0
                node["healthy"] = False
                node["last_check"] = time.time()
                print(f"[❌] 节点 {node['name']} 不健康，延迟: {delay}")
                return False

        except Exception as e:
            print(f"[⚠️] 检测节点 {node['name']} 健康状态失败: {e}")
            node["healthy"] = False
            node["delay"] = 0
            node["last_check"] = time.time()
            return False

    def get_current_node(self) -> Optional[Dict]:
        """获取当前节点信息"""
        if not self.available_nodes:
            return None

        # 确保current_node_index在有效范围内
        self._validate_node_index()

        return self.available_nodes[self.current_node_index]

    def get_proxy_config(self) -> Dict:
        """获取当前代理配置"""
        return {
            "http": f"http://127.0.0.1:{self.proxy_port}",
            "https": f"http://127.0.0.1:{self.proxy_port}"
        }

    def wait_for_switch_complete(self) -> None:
        if not self.enable_node_switching:
            return
        with self._switch_condition:
            while self._is_switching:
                self._switch_condition.wait()

    def _post_switch_connectivity_ok(self) -> bool:
        """通过当前代理发起快速连通性检测，返回是否可用。"""
        try:
            proxies = self.get_proxy_config()
            resp = requests.get(self.post_switch_test_url, proxies=proxies, timeout=self.post_switch_timeout_sec)
            return resp.status_code in (200, 204)
        except Exception:
            return False

    def increment_download_count(self) -> bool:
        """增加下载计数并检查是否需要切换节点"""
        self.download_count += 1

        current_node = self.get_current_node()
        if current_node:
            node_name = current_node["name"]
            self.node_download_counts[node_name] = self.node_download_counts.get(node_name, 0) + 1

        # 下载成功时重置403错误计数
        self.reset_403_error()

        # 保存状态
        self.save_state()

        # 检查是否需要切换节点
        if self.download_count >= self.max_downloads_per_node:
            return self.switch_node()
        return False

    def increment_403_error(self) -> bool:
        """增加403错误计数并检查是否需要切换节点"""
        self.error_403_count += 1
        print(f"[⚠️] 403错误计数: {self.error_403_count}/{self.max_403_errors}")

        if self.error_403_count >= self.max_403_errors:
            print(f"[🔄] 达到最大403错误数，切换节点")
            self.error_403_count = 0  # 重置计数
            return self.switch_node()
        return False

    def reset_403_error(self) -> None:
        """重置403错误计数（下载成功时调用）"""
        if self.error_403_count > 0:
            print(f"[✅] 下载成功，重置403错误计数: {self.error_403_count} -> 0")
            self.error_403_count = 0

    def switch_node(self, max_retries=None) -> bool:
        """切换节点（带线程阻塞）"""
        if not self.enable_node_switching or not self.available_nodes:
            print(f"[⚠️] 无法切换节点: 节点切换未启用或无可用节点")
            return False

        with self._switch_condition:
            while self._is_switching:
                self._switch_condition.wait()
            self._is_switching = True

        try:
            return self._perform_switch(max_retries)
        finally:
            with self._switch_condition:
                self._is_switching = False
                self._switch_condition.notify_all()

    def _get_verified_candidates(self) -> list:
        """返回已验证节点在 available_nodes 中的 name 列表（去重保序）"""
        available_names = {n["name"] for n in self.available_nodes}
        return [name for name in self.verified_nodes if name in available_names]

    def _perform_switch(self, max_retries=None) -> bool:
        if not self.available_nodes:
            print(f"[❌] 无可用节点")
            return False

        if max_retries is None:
            max_retries = min(self.post_switch_max_retries, len(self.available_nodes))
        if max_retries <= 0:
            print(f"[❌] 已尝试所有节点，切换失败")
            return False

        old_node = self.get_current_node()
        remaining = max_retries

        # 优先从已验证节点池中选
        verified_candidates = self._get_verified_candidates()
        if self.only_use_verified_nodes and verified_candidates:
            # 按 verified 列表顺序轮转
            old_name = old_node["name"] if old_node else ""
            if old_name in verified_candidates:
                idx = verified_candidates.index(old_name)
                verified_candidates = verified_candidates[idx + 1:] + verified_candidates[:idx]
            candidate_names = verified_candidates
            mode = "verified_only"
        else:
            candidate_names = [n["name"] for n in self.available_nodes]
            mode = "full_scan"

        if self.only_use_verified_nodes and not verified_candidates:
            print("[🔍] 没有已验证节点，先扫描所有节点构建验证池...")
            mode = "build_pool"

        # 先用候选列表，再补全扫描
        all_names = [n["name"] for n in self.available_nodes]
        tried_names = set()

        for phase in (["candidates", "rest"] if mode != "build_pool" else ["build"]):
            if phase == "candidates":
                names = candidate_names
            elif phase == "rest":
                names = [n for n in all_names if n not in tried_names]
            else:  # build
                names = all_names

            for node_name in names:
                if node_name in tried_names:
                    continue
                tried_names.add(node_name)

                node = next((n for n in self.available_nodes if n["name"] == node_name), None)
                if not node:
                    continue
                if old_node and node_name == old_node["name"] and len(self.available_nodes) > 1:
                    continue

                print(f"[🔍] 选择节点[{mode}]: {node_name}")
                print(f"[🔄] 切换节点: {old_node['name'] if old_node else 'N/A'} -> {node_name}")
                print(f"[📊] 当前下载计数: {self.download_count}, 已验证池: {len(self.verified_nodes)}")

                if self.pre_switch_delay_check:
                    pre_delay = self.get_node_delay(node_name)
                    if pre_delay is None or pre_delay <= 0:
                        print(f"[⏭️] 候选节点 Clash 延迟不可用，跳过: {node_name}")
                        continue
                    print(f"[✅] 候选节点延迟 OK: {node_name} ({pre_delay}ms)")

                if self.set_node_by_name(node_name):
                    print(f"[⏳] 等待节点切换完成...")
                    time.sleep(3)

                    # 验证节点切换
                    verify_success = False
                    for verify_attempt in range(3):
                        if self.verify_node_switch(node_name):
                            verify_success = True
                            break
                        if verify_attempt < 2:
                            print(f"[⏳] 验证失败，等待后重试 ({verify_attempt + 2}/3)...")
                            time.sleep(2)

                    # 出口连通性验证
                    if verify_success and self.require_post_switch_connectivity:
                        conn_ok = False
                        for conn_attempt in range(self.post_switch_max_retries):
                            if self._post_switch_connectivity_ok():
                                conn_ok = True
                                break
                            if conn_attempt < self.post_switch_max_retries - 1:
                                print(f"[⏳] 代理出口连通性检测失败，重试 ({conn_attempt + 2}/{self.post_switch_max_retries})...")
                                time.sleep(0.8)
                        if not conn_ok:
                            print(f"[⚠️] 节点 {node_name} 已选中但经代理无法访问外网，尝试下一候选")
                            self.verified_nodes.discard(node_name)
                            verify_success = False

                    if verify_success:
                        print(f"[✅] 节点切换成功: {node_name}")
                        self.verified_nodes.add(node_name)
                        # 同步 current_node_index
                        try:
                            self.current_node_index = self.available_nodes.index(node)
                        except ValueError:
                            self.current_node_index = 0
                        self.download_count = 0
                        self.error_403_count = 0
                        self.save_state()
                        return True
                    else:
                        remaining -= 1
                        print(f"[⚠️] 节点切换未通过，剩余重试: {remaining}")
                else:
                    remaining -= 1
                    print(f"[❌] set_node_by_name 失败，剩余重试: {remaining}")

                if remaining <= 0:
                    break

            if remaining <= 0:
                break

        print(f"[❌] 在 {max_retries} 次尝试后仍未切换成功")
        return False

    def verify_node_switch(self, expected_node_name: str) -> bool:
        """验证节点切换是否成功"""
        max_verify_attempts = 3

        for attempt in range(max_verify_attempts):
            try:
                # 准备请求头
                headers = {}
                if self.secret:
                    headers['Authorization'] = f'Bearer {self.secret}'

                # 临时禁用代理环境变量
                original_proxies = self._disable_proxy_env()

                try:
                    # 获取当前活跃的节点 - 使用策略组
                    verify_url = f"{self.api_url}/proxies/{self.group_name}"
                    response = requests.get(verify_url, headers=headers, timeout=5,
                                            proxies={"http": None, "https": None})
                finally:
                    # 恢复代理环境变量
                    self._restore_proxy_env(original_proxies)

                if response.status_code == 200:
                    data = response.json()
                    current_node = data.get("now", "")

                    # 调试信息
                    if attempt == 0:
                        continue
                        # print(f"[🔍] 验证节点切换: 期望={expected_node_name}, 实际={current_node}")

                    if current_node == expected_node_name:
                        # print(f"[✅] 节点切换验证成功!")
                        return True
                    else:
                        if attempt < max_verify_attempts - 1:
                            # print(f"[⚠️] 节点不匹配: 期望 {expected_node_name}, 实际 {current_node}，重试中...")
                            time.sleep(1)
                            continue
                        else:
                            print(f"[❌] 节点切换验证失败: 期望 {expected_node_name}, 实际 {current_node}")
                            # # 显示策略组的完整信息，帮助调试
                            # print(f"[🔍] 策略组 '{self.group_name}' 的完整信息:")
                            # print(f"     now: {current_node}")
                            # print(f"     all: {data.get('all', [])[:5]}...")  # 只显示前5个
                            return False
                else:
                    print(f"[⚠️] 无法获取当前节点信息: HTTP {response.status_code}")
                    if response.status_code == 404:
                        print(f"[💡] 提示: 策略组 '{self.group_name}' 不存在")
                    if attempt < max_verify_attempts - 1:
                        print(f"[🔄] 重试验证 ({attempt + 2}/{max_verify_attempts})...")
                        time.sleep(1)
                        continue
                    return False

            except Exception as e:
                if attempt < max_verify_attempts - 1:
                    print(f"[⚠️] 节点切换验证异常: {e}，重试中...")
                    time.sleep(1)
                    continue
                else:
                    print(f"[❌] 节点切换验证异常: {e}")
                    import traceback
                    traceback.print_exc()
                    return False

        return False

    def set_node_by_name(self, node_name: str) -> bool:
        """根据名称设置特定节点"""
        try:
            # 检查group_name是否有效（不能是GLOBAL）
            if not self.group_name or self.group_name.upper() == "GLOBAL":
                # print(f"[❌] 错误: 策略组名称不能是 'GLOBAL'，请使用具体的策略组名称")
                # print(f"[💡] 提示: 请检查配置中的 'group_name' 设置")
                # 尝试自动查找可用的策略组
                available_groups = self._find_available_groups()
                if available_groups:
                    self.group_name = available_groups[0]
                    # print(f"[✅] 自动切换到策略组: {self.group_name}")
                else:
                    print(f"[❌] 无法找到可用的策略组")
                    return False

            # 准备请求头
            headers = {}
            if self.secret:
                headers['Authorization'] = f'Bearer {self.secret}'

            # 临时禁用代理环境变量
            original_proxies = self._disable_proxy_env()

            try:
                # 通过Clash API设置节点 - 使用策略组而不是GLOBAL
                api_url = f"{self.api_url}/proxies/{self.group_name}"
                # print(f"[🔧] 调用API切换节点: PUT {api_url}")
                # print(f"[🔧] 请求体: {{\"name\": \"{node_name}\"}}")

                response = requests.put(
                    api_url,
                    json={"name": node_name},
                    headers=headers,
                    timeout=10,
                    proxies={"http": None, "https": None}
                )

                # print(f"[🔧] API响应状态码: {response.status_code}")
                # if response.status_code != 204:
                #     print(f"[🔧] API响应内容: {response.text[:200]}")
            finally:
                # 恢复代理环境变量
                self._restore_proxy_env(original_proxies)

            if response.status_code == 204:
                # print(f"[✅] API调用成功，已请求切换到节点: {node_name}")
                return True
            else:
                print(f"[❌] API调用失败: HTTP {response.status_code}")
                if response.status_code == 400:
                    print(f"[💡] 提示: 节点名称可能不存在于策略组 '{self.group_name}' 中")
                elif response.status_code == 404:
                    print(f"[💡] 提示: 策略组 '{self.group_name}' 不存在，尝试查找可用策略组...")
                    # 如果策略组不存在，尝试查找可用的策略组
                    available_groups = self._find_available_groups()
                    if available_groups:
                        # print(f"[🔍] 找到 {len(available_groups)} 个可用策略组:")
                        # for i, group in enumerate(available_groups[:5], 1):
                        #     print(f"    {i}. {group}")
                        self.group_name = available_groups[0]
                        # print(f"[✅] 自动切换到策略组: {self.group_name}")
                        # 重新尝试切换
                        return self.set_node_by_name(node_name)
                    else:
                        print(f"[❌] 未找到任何可用的策略组")
                return False

        except Exception as e:
            print(f"[‼] 切换节点异常: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_status(self) -> Dict:
        """获取状态信息"""
        current_node = self.get_current_node()

        return {
            "current_node": current_node["name"] if current_node else None,
            "download_count": self.download_count,
            "node_download_counts": self.node_download_counts.copy(),
            "available_nodes_count": len(self.available_nodes),
            "enable_node_switching": self.enable_node_switching,
            "error_403_count": self.error_403_count
        }

    def test_connection(self) -> bool:
        """测试Clash连接"""
        try:
            # 准备请求头
            headers = {}
            if self.secret:
                headers['Authorization'] = f'Bearer {self.secret}'

            # 临时禁用代理环境变量
            original_proxies = self._disable_proxy_env()

            try:
                # 强制使用直连，避免全局代理导致的502错误
                response = requests.get(f"{self.api_url}/version", headers=headers, timeout=5,
                                        proxies={"http": None, "https": None})
            finally:
                # 恢复代理环境变量
                self._restore_proxy_env(original_proxies)
            if response.status_code == 200:
                version = response.json().get("version", "Unknown")
                print(f"[✅] Clash连接正常，版本: {version}")
                return True
            else:
                print(f"[⚠️] Clash连接异常: {response.status_code}")
                return False
        except Exception as e:
            print(f"[‼] 无法连接到Clash: {e}")
            return False

    def get_node_delay(self, node_name: str) -> Optional[int]:
        """获取节点延迟"""
        try:
            import urllib.parse
            encoded_name = urllib.parse.quote(node_name)
            params = {"url": self.post_switch_test_url, "timeout": 5000}
            # params = {"url": "https://www.gstatic.com/generate_204", "timeout": 5000}
            # 准备请求头
            headers = {}
            if self.secret:
                headers['Authorization'] = f'Bearer {self.secret}'

            # 临时禁用代理环境变量
            original_proxies = self._disable_proxy_env()

            try:
                response = requests.get(
                    f"{self.api_url}/proxies/{encoded_name}/delay",
                    params=params,
                    headers=headers,
                    timeout=10,
                    proxies={"http": None, "https": None}
                )
            finally:
                # 恢复代理环境变量
                self._restore_proxy_env(original_proxies)

            if response.status_code == 200:
                data = response.json()
                return data.get("delay")
            return None
        except Exception as e:
            return None

    def refresh_node_health(self) -> bool:
        """手动刷新节点健康状态"""
        print("[🔄] 手动刷新节点健康状态...")
        try:
            self._check_node_health()
            print("[✅] 节点健康状态刷新完成")
            return True
        except Exception as e:
            print(f"[❌] 刷新节点健康状态失败: {e}")
            return False

    def get_healthy_nodes(self) -> List[Dict]:
        """获取所有健康节点"""
        return [node for node in self.available_nodes if node.get("healthy", False) and node.get("name", False)!= "DIRECT"]

    def get_unhealthy_nodes(self) -> List[Dict]:
        """获取所有不健康节点"""
        return [node for node in self.available_nodes if not node.get("healthy", False)]
    # def set_node_unhealthy_node(self, now_node: dict) -> None:
    #     # 删除当前
    #     for node in self.available_nodes:
    #         if node.get("name", False) == now_node.get("name", False):
    #             node["healthy"] = False
    def switch_to_healthy_node(self, record_now=False) -> bool:
        """切换到健康节点（优先从已验证池中选）"""
        # self._ensure_clash_matches_saved_index()
        self.healthy_node_idx += 1
        while True:
            now_node = self.get_current_node()
            if now_node and isinstance(now_node, dict):
                if record_now:
                    self.error_node_set.add_to_set(now_node.get("name", ""))
                if not self._post_switch_connectivity_ok():
                    self.verified_nodes.discard(now_node.get("name", ""))
                    if now_node in self.available_nodes:
                        print("当前节点 不可用 移除 再切换")
                        self.available_nodes.remove(now_node)
                    else:
                        self.refresh_nodes()
                        self.refresh_node_health()
            healthy_nodes = self.get_healthy_nodes()
            if len(healthy_nodes) < 5:
                print("[🔍] 健康节点不足，刷新节点列表和健康状态...")
                self.refresh_nodes()
                self.refresh_node_health()
                healthy_nodes = self.get_healthy_nodes()
            # 优先从已验证池中选健康节点
            if self.only_use_verified_nodes and self.verified_nodes:
                verified_healthy = [n for n in healthy_nodes if n["name"] in self.verified_nodes]
                candidates = verified_healthy if verified_healthy else healthy_nodes
            else:
                candidates = healthy_nodes
            if candidates:
                index = self.healthy_node_idx % len(candidates)
                healthy_node_name = candidates[index]["name"]
                print(f"[🔄] 切换到健康节点: {healthy_node_name}")
                self.set_node_by_name(healthy_node_name)
                time.sleep(2)
                is_ok = self._post_switch_connectivity_ok()
                if is_ok:
                    self.verified_nodes.add(healthy_node_name)
                    self.save_state()
                    break
                else:
                    self.verified_nodes.discard(healthy_node_name)
                    self.healthy_node_idx += 1
                    continue
            else:
                print("[⚠️] 没有可用的健康节点")
                return False

    def start_auto_health_check(self, interval_sec: int = 30):
        """启动后台健康检查线程，自动切换不可用节点"""

        def _check_loop():
            while True:
                time.sleep(interval_sec)
                if not self._post_switch_connectivity_ok():
                    print("[⚠️] 自动健康检查失败，切换节点")
                    self.switch_to_healthy_node()
                else:
                    print(f" {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  测试通过 ")

        thread = threading.Thread(target=_check_loop, daemon=True)
        thread.start()

        # 永久阻塞，不需要 while True
        threading.Event().wait()
        print(f"[🔁] 已启动自动健康检查，间隔 {interval_sec} 秒")


import time
import requests

post_switch_test_url = "https://www.jstage.jst.go.jp/"
# post_switch_test_url = 'https://dspace.cuni.cz/' # 全部不行 ping 都不行
# post_switch_test_url = "https://repositorio.unicamp.br",#ping 可以 但是503 换ip 无效
def main():
    config = {
        "api_url": "http://127.0.0.1:9097",
        "secret": "set-your-secret",
        "post_switch_test_url": post_switch_test_url, #"https://repositorio.unicamp.br",
        "group_name": "🚀节点选择",
        "proxy_port": 7897,
        "enable_node_switching": True,
        "max_downloads_per_node": 3,      # 测试用，下载3次就换节点
        "switch_strategy": "round_robin",
        "show_node_info": False,
    }
    manager = ClashManager(config)
    # if not manager.refresh_node_health():
    #     print("Clash 未运行或 API 不可达")
    #     return

    # manager.switch_to_healthy_node()


if __name__ == "__main__":
    main()

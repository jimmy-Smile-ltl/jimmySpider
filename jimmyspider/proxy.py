import json
import random
import time
import warnings

import redis
import requests
import threading

from jimmyspider.config import get_config

warnings.filterwarnings("ignore")


class RedisTokenBucket:
    LUA_SCRIPT = """
    local key = KEYS[1]
    local rate = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local need = tonumber(ARGV[4])

    local data = redis.call("HMGET", key, "tokens", "timestamp")
    local tokens = tonumber(data[1])
    local timestamp = tonumber(data[2])

    if tokens == nil then
        tokens = capacity
        timestamp = now
    end

    local delta = math.max(0, now - timestamp)
    tokens = math.min(capacity, tokens + delta * rate)
    local allowed = tokens >= need

    if allowed then
        tokens = tokens - need
    end

    redis.call("HMSET", key, "tokens", tokens, "timestamp", now)
    return allowed
    """

    def __init__(self, redis_client, key="project:token_bucket", rate=5, capacity=10):
        self.redis = redis_client
        self.key = key
        self.rate = rate
        self.capacity = capacity
        self._script = self.redis.register_script(self.LUA_SCRIPT)

    def try_acquire(self, tokens=1):
        now = int(time.time())
        return bool(self._script(keys=[self.key], args=[self.rate, self.capacity, now, tokens]))

    def acquire(self, tokens=1, interval=0.1):
        while True:
            if self.try_acquire(tokens):
                return True
            time.sleep(interval)


class ProxyManager:
    """
    单例模式 ProxyManager，支持 Redis 存储字典代理 + 阻塞/非阻塞限流
    """
    _instance_lock = threading.Lock()
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._instance_lock:
                if not cls._instance:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, redis_client, limiter: RedisTokenBucket, redis_key="proxy:list"):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self.redis = redis_client
        self.limiter = limiter
        self.redis_key = redis_key
        self._initialized = True

    def _get_all_proxies(self):
        proxies = self.redis.lrange(self.redis_key, 0, -1)
        return [json.loads(p) for p in proxies]

    def get_proxy(self):
        """阻塞模式获取代理"""
        self.limiter.acquire()
        proxies = self._get_all_proxies()
        return random.choice(proxies) if proxies else None

    def try_get_proxy(self):
        """非阻塞模式获取代理"""
        if self.limiter.try_acquire():
            proxies = self._get_all_proxies()
            return random.choice(proxies) if proxies else None
        return None


# 全局初始化函数，保证单例
_global_proxy_manager = None
_global_lock = threading.Lock()


def get_proxy_manager(redis_host="127.0.0.1", redis_port=6379, redis_password=None,
                      rate=5, capacity=5, redis_key="proxy:list"):
    global _global_proxy_manager
    if _global_proxy_manager is None:
        with _global_lock:
            if _global_proxy_manager is None:
                r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True, password=redis_password)
                limiter = RedisTokenBucket(r, key=f"{redis_key}:bucket", rate=rate, capacity=capacity)
                _global_proxy_manager = ProxyManager(r, limiter, redis_key=redis_key)
    return _global_proxy_manager


class ProxyUtil():
    def __init__(self, test_url, headers=None, cookies=None):
        self.test_url = test_url
        self.cookies = cookies
        if headers is None:
            self.headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
            }

    def get_proxy(self):
        # proxy = self._fetch_new_proxy()
        proxy = self.get_proxy_tunel()
        # proxy = self.proxy_manager.get_proxy()
        return proxy

    def get_clash_proxy(self):
        config = get_config()
        clash_url = config.CLASH_PROXY_URL
        return {"http": clash_url, "https": clash_url}

    def get_proxy_tunel(self):
        config = get_config()
        tunnel_url = config.PROXY_TUNNEL_URL
        if tunnel_url:
            return {"http": tunnel_url, "https": tunnel_url}
        return {}

    def test_proxy(self, proxies):
        try:
            response = requests.get(
                url=self.test_url,
                proxies=proxies,
                timeout=5,
                allow_redirects=True,
                verify=True,
                headers=self.headers,
                cookies=self.cookies
            )
            if response.status_code == 200 or response.status_code == 304:
                return True
            else:
                return False
        except Exception as e:
            return False

    def _fetch_new_proxy(self):
        retry_count = 200
        for i in range(retry_count):
            try:
                api_url = get_config().PROXY_API_URL
                if not api_url:
                    return {}
                url = api_url
                res = requests.get(url=url)
                if res.status_code != 200 or "502" in res.text or "1040" in res.text:
                    time.sleep(random.uniform(1, 3))
                    # print("获取代理失败:", res.text)
                    continue
                proxy_list = json.loads(res.content.decode("utf-8"))["data"].get("proxy_list")
                proxies = {
                    "http": f"http://{proxy_list[0]}",
                    "https": f"http://{proxy_list[0]}",
                }
                if self.test_proxy(proxies=proxies):
                    return proxies
                else:
                    # 测试失败
                    if i > 9 and i % 10 == 0:
                        print(f"第{i} /{retry_count}次获取代理失败，继续尝试...")
                    continue
            except Exception as e:
                # print(f"获取代理失败，错误: {e}")
                time.sleep(random.uniform(1, 3))
        return {}

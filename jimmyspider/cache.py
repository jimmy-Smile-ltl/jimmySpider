"""
jimmySpider - Redis 缓存模块

提供基于 Redis 的断点续爬、进度记录、URL 去重等功能。
配置通过环境变量设置，详见 jimmyspider.config。
"""

import json
import warnings

import redis

from jimmyspider.config import get_config

warnings.filterwarnings("ignore")


class Cache:
    """Redis 缓存封装，用于断点续爬和进度管理。

    使用方式:
        page_cache = Cache("myproject_log_page")
        page_cache.record_int(5)
        current = page_cache.get_int(default=1)
    """

    def __init__(self, key: str):
        config = get_config()
        self.redis_client = redis.StrictRedis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            db=config.REDIS_DB,
            password=config.REDIS_PASSWORD,
            decode_responses=True,
        )
        self.key = key

    def get_redis_client(self):
        """获取 Redis 客户端"""
        return self.redis_client

    def shutdown(self):
        """关闭 Redis 连接"""
        try:
            self.redis_client.close()
        except Exception as e:
            print(f"关闭Redis连接失败: {e}")

    def record_int(self, value: int) -> None:
        try:
            self.redis_client.set(self.key, value)
        except Exception as e:
            print(f"记录进度失败: {e}")

    def get_int(self, default=1) -> int:
        try:
            value = self.redis_client.get(self.key)
            return int(value) if value else default
        except Exception as e:
            print(f"获取进度失败: {e}")
            return default

    def record_string(self, value: str) -> None:
        try:
            self.redis_client.set(self.key, value)
        except Exception as e:
            print(f"记录进度失败: {e}")

    def get_string(self, default="") -> str:
        try:
            value = self.redis_client.get(self.key)
            return value if value else default
        except Exception as e:
            print(f"获取进度失败: {e}")
            return default

    def clear_value(self) -> None:
        try:
            self.redis_client.delete(self.key)
        except Exception as e:
            print(f"清除进度失败: {e}")

    def record_list(self, value: list) -> None:
        try:
            if not isinstance(value, list):
                raise ValueError("value must be a list")
            if len(value) == 0:
                raise ValueError("value list cannot be empty")
            self.redis_client.rpush(self.key, *value)
        except Exception as e:
            print(f"记录列表失败: {e}")

    def get_list(self, default=None) -> list:
        if default is None:
            default = []
        try:
            value = self.redis_client.lrange(self.key, 0, -1)
            return value if value else default
        except Exception as e:
            print(f"获取列表失败: {e}")
            return default

    def append_to_list(self, value) -> None:
        try:
            self.redis_client.rpush(self.key, str(value))
        except Exception as e:
            print(f"追加到列表失败: {e}")

    def remove_from_list(self, value: str) -> None:
        try:
            self.redis_client.lrem(self.key, 0, value)
        except Exception as e:
            print(f"从列表中移除失败: {e}")

    def get_list_length(self) -> int:
        try:
            return self.redis_client.llen(self.key)
        except Exception as e:
            print(f"获取列表长度失败: {e}")
            return 0

    def clear_list(self, method: str = "trim") -> None:
        """清空 Redis 列表。

        :param method: 'trim' (保留键但清空内容) 或 'delete' (直接删除键)
        """
        try:
            if method == "trim":
                self.redis_client.ltrim(self.key, 1, 0)
                print(f"列表键 '{self.key}' 已成功清空 (使用LTRIM)。")
            elif method == "delete":
                self.redis_client.delete(self.key)
                print(f"列表键 '{self.key}' 已成功删除。")
            else:
                print(f"错误：无效的清空方法 '{method}'。请选择 'trim' 或 'delete'。")
        except Exception as e:
            print(f"清空列表失败: {e}")

    def add_to_set(self, value) -> None:
        """向集合添加元素"""
        try:
            if isinstance(value, str):
                self.redis_client.sadd(self.key, value)
            elif isinstance(value, (list, dict)):
                value_str = json.dumps(value)
                self.redis_client.sadd(self.key, value_str)
            else:
                self.redis_client.sadd(self.key, str(value))
        except Exception as e:
            print(f"向集合添加元素失败: {e}")

    def remove_from_set(self, value) -> None:
        """从集合中移除元素"""
        try:
            self.redis_client.srem(self.key, value)
        except Exception as e:
            print(f"从集合中移除元素失败: {e}")

    def is_member_of_set(self, value) -> bool:
        """检查元素是否是集合的成员"""
        try:
            return self.redis_client.sismember(self.key, value)
        except Exception as e:
            print(f"检查集合成员失败: {e}")
            return False

    def get_set_members(self) -> set:
        """获取集合的所有成员"""
        try:
            return self.redis_client.smembers(self.key)
        except Exception as e:
            print(f"获取集合成员失败: {e}")
            return set()

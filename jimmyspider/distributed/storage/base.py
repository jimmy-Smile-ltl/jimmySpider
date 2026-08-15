"""
分布式储存 — 抽象接口

统一 MongoDB / PostgreSQL / MySQL / Elasticsearch 的操作接口。
参考 jimmyspider/mongo.py 的 HandleMongoDB 设计。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Callable


@dataclass
class StorageRecord:
    """统一存储记录"""
    _id: str                                      # 主键
    data: dict = field(default_factory=dict)      # 数据
    collection: str = "default"                   # 集合/表名
    version: int = 1
    created_at: float = 0.0
    updated_at: float = 0.0


class StorageBackend(ABC):
    """
    储存后端抽象基类

    所有数据库后端（MongoDB/PostgreSQL/MySQL/ES）必须实现此接口。

    核心操作:
      - insert_one / insert_many: 写入
      - find_one / find_many: 查询
      - update_one / update_many / upsert: 更新
      - delete_one / delete_many: 删除
      - count: 计数
      - bulk_write: 批量写入
    """

    backend_name: str = "base"

    # ---- 写入 ----
    @abstractmethod
    async def insert_one(self, collection: str, record: dict, id_field: str = "_id") -> str:
        """插入单条 → 返回 _id"""
        ...

    @abstractmethod
    async def insert_many(self, collection: str, records: list[dict],
                          id_field: str = "_id") -> int:
        """批量插入 → 返回插入数量"""
        ...

    @abstractmethod
    async def upsert(self, collection: str, record: dict,
                     id_field: str = "_id") -> bool:
        """插入或更新 → 返回是否新插入"""
        ...

    # ---- 查询 ----
    @abstractmethod
    async def find_one(self, collection: str, filter: dict) -> Optional[dict]:
        """查询单条"""
        ...

    @abstractmethod
    async def find_many(self, collection: str, filter: dict,
                        sort: list = None, skip: int = 0,
                        limit: int = 100) -> list[dict]:
        """查询多条"""
        ...

    # ---- 更新 ----
    @abstractmethod
    async def update_one(self, collection: str, filter: dict,
                         update: dict) -> int:
        """更新单条 → 返回更新数量"""
        ...

    @abstractmethod
    async def update_many(self, collection: str, filter: dict,
                          update: dict) -> int:
        """更新多条 → 返回更新数量"""
        ...

    # ---- 删除 ----
    @abstractmethod
    async def delete_one(self, collection: str, filter: dict) -> int:
        """删除单条"""
        ...

    @abstractmethod
    async def delete_many(self, collection: str, filter: dict) -> int:
        """删除多条"""
        ...

    # ---- 聚合 ----
    @abstractmethod
    async def count(self, collection: str, filter: dict = None) -> int:
        """计数"""
        ...

    @abstractmethod
    async def aggregate(self, collection: str, pipeline: list) -> list[dict]:
        """聚合查询"""
        ...

    # ---- 批量 ----
    @abstractmethod
    async def bulk_write(self, collection: str, operations: list[dict]) -> int:
        """
        批量写入（upsert）

        operations: [{"action": "upsert", "_id": "xxx", "data": {...}}, ...]
        """
        ...

    # ---- 管理 ----
    @abstractmethod
    async def health_check(self) -> dict:
        """健康检查"""
        ...

    @abstractmethod
    async def create_index(self, collection: str, keys: list[tuple],
                           unique: bool = False) -> None:
        """创建索引"""
        ...

    @abstractmethod
    async def close(self) -> None:
        """关闭连接"""
        ...

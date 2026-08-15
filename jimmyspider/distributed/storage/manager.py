"""
分布式储存管理器

统一管理多个数据库后端，支持:
  - 多后端写入（主储存 + 备份储存）
  - 读写分离（主写从读）
  - 自动路由（按条件选择后端）
  - 数据迁移工具

参考: jimmyspider/mongo.py HandleMongoDB 的批量操作、断点续传设计
"""

import asyncio
import time
from typing import Optional, Callable

from .base import StorageBackend


class DistributedStorageManager:
    """
    分布式储存管理器

    策略:
      - primary_only: 只用主后端
      - dual_write: 双写（主+备份），从主读
      - read_write_split: 读写分离（写主，读从）
      - shard_by_collection: 按 collection 分片到不同后端

    使用:
        mgr = DistributedStorageManager(strategy="dual_write")
        mgr.set_primary(mongodb)
        mgr.set_backup(postgresql)
        await mgr.insert_one("reports", {...})
    """

    def __init__(self, strategy: str = "primary_only"):
        """
        Args:
            strategy: primary_only / dual_write / read_write_split / shard_by_collection
        """
        self.strategy = strategy
        self._primary: Optional[StorageBackend] = None
        self._backup: Optional[StorageBackend] = None
        self._read_backends: list[StorageBackend] = []
        self._shard_map: dict[str, StorageBackend] = {}  # collection → backend

    def set_primary(self, backend: StorageBackend) -> "DistributedStorageManager":
        self._primary = backend
        return self

    def set_backup(self, backend: StorageBackend) -> "DistributedStorageManager":
        self._backup = backend
        return self

    def add_read_backend(self, backend: StorageBackend) -> "DistributedStorageManager":
        self._read_backends.append(backend)
        return self

    def shard_collection(self, collection: str, backend: StorageBackend) -> "DistributedStorageManager":
        self._shard_map[collection] = backend
        return self

    def _resolve(self, collection: str) -> StorageBackend:
        """解析 collection 对应的后端"""
        if self.strategy == "shard_by_collection" and collection in self._shard_map:
            return self._shard_map[collection]
        return self._primary

    async def _write_both(self, collection: str, method: str, *args, **kwargs):
        """双写: 主+备份"""
        result = await getattr(self._primary, method)(collection, *args, **kwargs)
        if self._backup:
            try:
                await getattr(self._backup, method)(collection, *args, **kwargs)
            except Exception:
                pass  # 备份写入失败不影响主
        return result

    # ---- 写入 ----
    async def insert_one(self, collection: str, record: dict, **kw) -> str:
        if self.strategy == "dual_write":
            return await self._write_both(collection, "insert_one", record, **kw)
        return await self._resolve(collection).insert_one(collection, record, **kw)

    async def insert_many(self, collection: str, records: list[dict], **kw) -> int:
        if self.strategy == "dual_write":
            return await self._write_both(collection, "insert_many", records, **kw)
        return await self._resolve(collection).insert_many(collection, records, **kw)

    async def upsert(self, collection: str, record: dict, **kw) -> bool:
        if self.strategy == "dual_write":
            return await self._write_both(collection, "upsert", record, **kw)
        return await self._resolve(collection).upsert(collection, record, **kw)

    # ---- 查询 ----
    async def find_one(self, collection: str, filter: dict) -> Optional[dict]:
        if self.strategy == "read_write_split" and self._read_backends:
            return await self._read_backends[0].find_one(collection, filter)
        return await self._resolve(collection).find_one(collection, filter)

    async def find_many(self, collection: str, filter: dict, **kw) -> list[dict]:
        if self.strategy == "read_write_split" and self._read_backends:
            return await self._read_backends[0].find_many(collection, filter, **kw)
        return await self._resolve(collection).find_many(collection, filter, **kw)

    # ---- 批量 ----
    async def bulk_write(self, collection: str, operations: list[dict]) -> int:
        if self.strategy == "dual_write":
            return await self._write_both(collection, "bulk_write", operations)
        return await self._resolve(collection).bulk_write(collection, operations)

    # ---- 更新 / 删除 / 计数 / 聚合 —— 代理到对应后端 ----
    async def update_one(self, collection: str, filter: dict, update: dict) -> int:
        return await self._resolve(collection).update_one(collection, filter, update)

    async def update_many(self, collection: str, filter: dict, update: dict) -> int:
        return await self._resolve(collection).update_many(collection, filter, update)

    async def delete_one(self, collection: str, filter: dict) -> int:
        return await self._resolve(collection).delete_one(collection, filter)

    async def delete_many(self, collection: str, filter: dict) -> int:
        return await self._resolve(collection).delete_many(collection, filter)

    async def count(self, collection: str, filter: dict = None) -> int:
        return await self._resolve(collection).count(collection, filter)

    async def aggregate(self, collection: str, pipeline: list) -> list[dict]:
        return await self._resolve(collection).aggregate(collection, pipeline)

    # ---- 健康检查 ----
    async def health_check(self) -> dict:
        results = {}
        for name, backend in [("primary", self._primary), ("backup", self._backup)]:
            if backend:
                results[name] = await backend.health_check()
        return {"strategy": self.strategy, "backends": results}

    async def close(self) -> None:
        for backend in [self._primary, self._backup, *self._read_backends, *self._shard_map.values()]:
            if backend:
                await backend.close()

    # ---- 数据迁移 ----
    async def migrate_collection(self, collection: str, source: StorageBackend,
                                 target: StorageBackend, batch_size: int = 1000,
                                 progress_cb: Callable = None) -> int:
        """将 collection 从 source 迁移到 target"""
        total = 0
        skip = 0
        while True:
            batch = await source.find_many(collection, {}, skip=skip, limit=batch_size)
            if not batch:
                break
            await target.insert_many(collection, batch)
            total += len(batch)
            skip += batch_size
            if progress_cb:
                progress_cb(total)
        return total

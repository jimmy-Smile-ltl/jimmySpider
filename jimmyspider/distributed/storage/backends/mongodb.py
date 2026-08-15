"""
MongoDB 储存后端

适配现有 jimmyspider/mongo.py HandleMongoDB 的接口，增加:
  - 异步支持 (motor)
  - 连接池管理
  - 自动重连

配置来源: jimmyspider 全局配置（MONGO_URI / MONGO_DB，可选依赖 motor）
参考: jimmyspider/mongo.py HandleMongoDB
"""

from typing import Any, Optional

from pymongo import UpdateOne, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure

from ..base import StorageBackend
from jimmyspider.config import get_config


class MongoDBBackend(StorageBackend):
    """
    MongoDB 异步后端

    配置:
      uri: MongoDB 连接串（默认取全局配置 MONGO_URI）
      database: 数据库名（默认取全局配置 MONGO_DB）
      max_pool_size: 连接池大小
    """

    backend_name = "mongodb"

    def __init__(self, uri: str = None,
                 database: str = None,
                 max_pool_size: int = 50):
        cfg = get_config()
        self.uri = uri or cfg.MONGO_URI
        self.database_name = database or cfg.MONGO_DB
        self.max_pool_size = max_pool_size
        self._client: Optional[Any] = None
        self._db = None

    async def _get_db(self):
        if self._client is None:
            from motor.motor_asyncio import AsyncIOMotorClient
            self._client = AsyncIOMotorClient(
                self.uri,
                maxPoolSize=self.max_pool_size,
                serverSelectionTimeoutMS=5000,
            )
            self._db = self._client[self.database_name]
        return self._db

    def _coll(self, db, collection: str):
        return db[collection]

    # ---- 写入 ----
    async def insert_one(self, collection: str, record: dict,
                         id_field: str = "_id") -> str:
        db = await self._get_db()
        if id_field in record:
            record["_id"] = record.pop(id_field)
        result = await self._coll(db, collection).insert_one(record)
        return str(result.inserted_id)

    async def insert_many(self, collection: str, records: list[dict],
                          id_field: str = "_id") -> int:
        db = await self._get_db()
        # 去重（保留最后出现的 _id）
        seen = {}
        for i, r in enumerate(records):
            rid = r.get(id_field, r.get("_id", ""))
            if rid:
                if "_id" not in r and id_field in r:
                    r["_id"] = r.pop(id_field)
                seen[r.get("_id", rid)] = i
        deduped = [records[i] for i in seen.values()]

        if not deduped:
            return 0
        result = await self._coll(db, collection).insert_many(deduped, ordered=False)
        return len(result.inserted_ids)

    async def upsert(self, collection: str, record: dict,
                     id_field: str = "_id") -> bool:
        db = await self._get_db()
        rid = record.get(id_field, record.get("_id", ""))
        if id_field in record and "_id" not in record:
            record["_id"] = record.pop(id_field)
        result = await self._coll(db, collection).update_one(
            {"_id": record.get("_id", rid)},
            {"$set": record}, upsert=True
        )
        return result.upserted_id is not None

    # ---- 查询 ----
    async def find_one(self, collection: str, filter: dict) -> Optional[dict]:
        db = await self._get_db()
        doc = await self._coll(db, collection).find_one(filter)
        return doc

    async def find_many(self, collection: str, filter: dict,
                        sort: list = None, skip: int = 0,
                        limit: int = 100) -> list[dict]:
        db = await self._get_db()
        cursor = self._coll(db, collection).find(filter).skip(skip).limit(limit)
        if sort:
            cursor = cursor.sort(sort)
        return await cursor.to_list(length=limit)

    # ---- 更新 ----
    async def update_one(self, collection: str, filter: dict,
                         update: dict) -> int:
        db = await self._get_db()
        result = await self._coll(db, collection).update_one(filter, {"$set": update})
        return result.modified_count

    async def update_many(self, collection: str, filter: dict,
                          update: dict) -> int:
        db = await self._get_db()
        result = await self._coll(db, collection).update_many(filter, {"$set": update})
        return result.modified_count

    # ---- 删除 ----
    async def delete_one(self, collection: str, filter: dict) -> int:
        db = await self._get_db()
        result = await self._coll(db, collection).delete_one(filter)
        return result.deleted_count

    async def delete_many(self, collection: str, filter: dict) -> int:
        db = await self._get_db()
        result = await self._coll(db, collection).delete_many(filter)
        return result.deleted_count

    # ---- 聚合 ----
    async def count(self, collection: str, filter: dict = None) -> int:
        db = await self._get_db()
        return await self._coll(db, collection).count_documents(filter or {})

    async def aggregate(self, collection: str, pipeline: list) -> list[dict]:
        db = await self._get_db()
        cursor = self._coll(db, collection).aggregate(pipeline)
        return await cursor.to_list(length=None)

    # ---- 批量 ----
    async def bulk_write(self, collection: str, operations: list[dict]) -> int:
        """批量 upsert（兼容 HandleMongoDB.insert_many 风格）"""
        db = await self._get_db()
        ops = []
        for op in operations:
            if op.get("action") == "upsert":
                ops.append(UpdateOne(
                    {"_id": op["_id"]},
                    {"$set": op.get("data", op)},
                    upsert=True
                ))
        if not ops:
            return 0
        result = await self._coll(db, collection).bulk_write(ops, ordered=False)
        return result.upserted_count + result.modified_count

    # ---- 管理 ----
    async def health_check(self) -> dict:
        try:
            db = await self._get_db()
            await db.command("ping")
            info = await db.command("serverStatus")
            return {
                "backend": self.backend_name,
                "status": "healthy",
                "connections": info.get("connections", {}).get("current", 0),
                "version": info.get("version", "unknown"),
            }
        except Exception as e:
            return {"backend": self.backend_name, "status": "unhealthy", "error": str(e)}

    async def create_index(self, collection: str, keys: list[tuple],
                           unique: bool = False) -> None:
        db = await self._get_db()
        index_keys = [(k, ASCENDING) for k in keys] if isinstance(keys[0], str) else keys
        await self._coll(db, collection).create_index(index_keys, unique=unique)

    async def close(self) -> None:
        if self._client:
            self._client.close()

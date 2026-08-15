"""
MySQL 储存后端

使用 aiomysql 异步驱动，JSON 字段存储数据。

配置来源: jimmyspider 全局配置（MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB，可选依赖 aiomysql）
"""

from typing import Optional
import json

from ..base import StorageBackend
from jimmyspider.config import get_config


class MySQLBackend(StorageBackend):
    backend_name = "mysql"

    def __init__(self, host: str = None, port: int = None,
                 user: str = None, password: str = None,
                 database: str = None,
                 pool_size: int = 10):
        cfg = get_config()
        self.host = host or cfg.MYSQL_HOST
        self.port = port or cfg.MYSQL_PORT
        self.user = user or cfg.MYSQL_USER
        self.password = password if password is not None else cfg.MYSQL_PASSWORD
        self.database = database or cfg.MYSQL_DB
        self.pool_size = pool_size
        self._pool: Optional[object] = None

    async def _get_pool(self):
        if self._pool is None:
            import aiomysql
            self._pool = await aiomysql.create_pool(
                host=self.host, port=self.port,
                user=self.user, password=self.password,
                db=self.database, minsize=1, maxsize=self.pool_size,
                autocommit=True)
        return self._pool

    async def _ensure_table(self, collection: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS `{collection}` (
                        _id VARCHAR(255) PRIMARY KEY,
                        data JSON NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)

    async def insert_one(self, collection: str, record: dict, id_field: str = "_id") -> str:
        await self._ensure_table(collection)
        rid = record.pop(id_field, record.get("_id", ""))
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"INSERT INTO `{collection}` (_id, data) VALUES (%s, %s) "
                    f"ON DUPLICATE KEY UPDATE data = VALUES(data)",
                    (rid, json.dumps(record, ensure_ascii=False)))
        return rid

    async def insert_many(self, collection: str, records: list[dict], id_field: str = "_id") -> int:
        count = 0
        for r in records:
            await self.insert_one(collection, r, id_field)
            count += 1
        return count

    async def upsert(self, collection: str, record: dict, id_field: str = "_id") -> bool:
        await self.insert_one(collection, record, id_field)
        return True

    async def find_one(self, collection: str, filter: dict) -> Optional[dict]:
        pool = await self._get_pool()
        where, params = self._build_where(filter)
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(f"SELECT * FROM `{collection}` WHERE {where} LIMIT 1", params)
                row = await cur.fetchone()
        return self._row_to_dict(row) if row else None

    async def find_many(self, collection: str, filter: dict, sort: list = None,
                        skip: int = 0, limit: int = 100) -> list[dict]:
        pool = await self._get_pool()
        where, params = self._build_where(filter)
        sql = f"SELECT * FROM `{collection}` WHERE {where}"
        if sort:
            sql += " ORDER BY " + ", ".join(f"{s[0]} {s[1]}" for s in sort)
        sql += f" LIMIT {limit} OFFSET {skip}"
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def update_one(self, collection: str, filter: dict, update: dict) -> int:
        pool = await self._get_pool()
        where, wparams = self._build_where(filter)
        sql = f"UPDATE `{collection}` SET data = JSON_MERGE_PATCH(data, %s) WHERE {where}"
        params = [json.dumps(update, ensure_ascii=False)] + wparams
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                return cur.rowcount

    async def update_many(self, collection: str, filter: dict, update: dict) -> int:
        return await self.update_one(collection, filter, update)

    async def delete_one(self, collection: str, filter: dict) -> int:
        pool = await self._get_pool()
        where, params = self._build_where(filter)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"DELETE FROM `{collection}` WHERE {where}", params)
                return cur.rowcount

    async def delete_many(self, collection: str, filter: dict) -> int:
        return await self.delete_one(collection, filter)

    async def count(self, collection: str, filter: dict = None) -> int:
        pool = await self._get_pool()
        if filter:
            where, params = self._build_where(filter)
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT COUNT(*) FROM `{collection}` WHERE {where}", params)
                    row = await cur.fetchone()
                    return row[0] if row else 0
        else:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT COUNT(*) FROM `{collection}`")
                    row = await cur.fetchone()
                    return row[0] if row else 0

    async def aggregate(self, collection: str, pipeline: list) -> list[dict]:
        raise NotImplementedError

    async def bulk_write(self, collection: str, operations: list[dict]) -> int:
        count = 0
        for op in operations:
            await self.upsert(collection, op.get("data", {}))
            count += 1
        return count

    async def health_check(self) -> dict:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT VERSION()")
                    row = await cur.fetchone()
            return {"backend": self.backend_name, "status": "healthy", "version": row[0] if row else "unknown"}
        except Exception as e:
            return {"backend": self.backend_name, "status": "unhealthy", "error": str(e)}

    async def create_index(self, collection: str, keys: list[tuple], unique: bool = False) -> None:
        pool = await self._get_pool()
        cols = ", ".join(f"((JSON_UNQUOTE(JSON_EXTRACT(data, '$.{k[0]}'))))" for k in keys)
        unique_str = "UNIQUE" if unique else ""
        idx_name = f"idx_{collection}_" + "_".join(k[0] for k in keys)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(f"CREATE {unique_str} INDEX `{idx_name}` ON `{collection}` ({cols})")

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()

    def _build_where(self, filter: dict) -> tuple[str, list]:
        if not filter: return "TRUE", []
        conds, params = [], []
        for k, v in filter.items():
            if k == "_id": conds.append("_id = %s")
            else: conds.append("JSON_UNQUOTE(JSON_EXTRACT(data, %s)) = %s"); params.append(f"$.{k}")
            params.append(str(v))
        return " AND ".join(conds), params

    @staticmethod
    def _row_to_dict(row) -> dict:
        if not row: return {}
        result = {"_id": row["_id"], "created_at": str(row.get("created_at", "")),
                  "updated_at": str(row.get("updated_at", ""))}
        data = row.get("data", {})
        if isinstance(data, str): data = json.loads(data)
        result.update(data)
        return result

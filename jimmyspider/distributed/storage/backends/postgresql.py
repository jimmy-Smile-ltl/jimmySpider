"""
PostgreSQL 储存后端

使用 asyncpg 异步驱动，JSONB 字段存储数据。
兼容现有的 dict 操作接口。

配置来源: jimmyspider 全局配置（PG_HOST / PG_PORT / PG_USER / PG_PASSWORD / PG_DB，可选依赖 asyncpg）
"""

from typing import Any, Optional

from ..base import StorageBackend
from jimmyspider.config import get_config


class PostgreSQLBackend(StorageBackend):
    """
    PostgreSQL 异步后端

    表结构 (自动创建):
      CREATE TABLE IF NOT EXISTS {collection} (
        _id TEXT PRIMARY KEY,
        data JSONB NOT NULL DEFAULT '{}',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
      )

    配置:
      dsn: PostgreSQL 连接串（默认按全局配置 PG_* 拼接）
      min_size / max_size: 连接池大小
    """

    backend_name = "postgresql"

    def __init__(self, dsn: str = None,
                 min_size: int = 5, max_size: int = 20):
        cfg = get_config()
        if dsn is None:
            dsn = (f"postgresql://{cfg.PG_USER}:{cfg.PG_PASSWORD}"
                   f"@{cfg.PG_HOST}:{cfg.PG_PORT}/{cfg.PG_DB}")
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Optional[Any] = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self.dsn, min_size=self.min_size, max_size=self.max_size
            )
        return self._pool

    async def _ensure_table(self, collection: str) -> None:
        pool = await self._get_pool()
        await pool.execute(f"""
            CREATE TABLE IF NOT EXISTS "{collection}" (
                _id TEXT PRIMARY KEY,
                data JSONB NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

    # ---- 写入 ----
    async def insert_one(self, collection: str, record: dict,
                         id_field: str = "_id") -> str:
        await self._ensure_table(collection)
        pool = await self._get_pool()
        rid = record.pop(id_field, record.get("_id", ""))
        import json
        await pool.execute(
            f'INSERT INTO "{collection}" (_id, data) VALUES ($1, $2) '
            f'ON CONFLICT (_id) DO UPDATE SET data = $2, updated_at = NOW()',
            rid, json.dumps(record, ensure_ascii=False)
        )
        return rid

    async def insert_many(self, collection: str, records: list[dict],
                          id_field: str = "_id") -> int:
        await self._ensure_table(collection)
        pool = await self._get_pool()
        import json
        count = 0
        async with pool.acquire() as conn:
            async with conn.transaction():
                for r in records:
                    rid = r.pop(id_field, r.get("_id", ""))
                    await conn.execute(
                        f'INSERT INTO "{collection}" (_id, data) VALUES ($1, $2) '
                        f'ON CONFLICT (_id) DO UPDATE SET data = $2, updated_at = NOW()',
                        rid, json.dumps(r, ensure_ascii=False)
                    )
                    count += 1
        return count

    async def upsert(self, collection: str, record: dict,
                     id_field: str = "_id") -> bool:
        await self._ensure_table(collection)
        pool = await self._get_pool()
        rid = record.pop(id_field, record.get("_id", ""))
        import json
        await pool.execute(
            f'INSERT INTO "{collection}" (_id, data) VALUES ($1, $2) '
            f'ON CONFLICT (_id) DO UPDATE SET data = $2, updated_at = NOW()',
            rid, json.dumps(record, ensure_ascii=False)
        )
        return True

    # ---- 查询 ----
    async def find_one(self, collection: str, filter: dict) -> Optional[dict]:
        pool = await self._get_pool()
        where, params = self._build_where(filter)
        row = await pool.fetchrow(
            f'SELECT * FROM "{collection}" WHERE {where} LIMIT 1', *params
        )
        return self._row_to_dict(row) if row else None

    async def find_many(self, collection: str, filter: dict,
                        sort: list = None, skip: int = 0,
                        limit: int = 100) -> list[dict]:
        pool = await self._get_pool()
        where, params = self._build_where(filter)
        sql = f'SELECT * FROM "{collection}" WHERE {where}'
        if sort:
            order = ", ".join(f"{s[0]} {s[1]}" for s in sort)
            sql += f" ORDER BY {order}"
        sql += f" OFFSET {skip} LIMIT {limit}"
        rows = await pool.fetch(sql, *params)
        return [self._row_to_dict(r) for r in rows]

    async def update_one(self, collection: str, filter: dict,
                         update: dict) -> int:
        pool = await self._get_pool()
        import json
        where, params = self._build_where(filter)
        result = await pool.execute(
            f'UPDATE "{collection}" SET data = data || $1::jsonb, '
            f'updated_at = NOW() WHERE {where}',
            json.dumps(update, ensure_ascii=False), *params
        )
        return int(result.split()[-1]) if result else 0

    async def update_many(self, collection: str, filter: dict,
                          update: dict) -> int:
        return await self.update_one(collection, filter, update)

    async def delete_one(self, collection: str, filter: dict) -> int:
        pool = await self._get_pool()
        where, params = self._build_where(filter)
        result = await pool.execute(
            f'DELETE FROM "{collection}" WHERE {where}', *params
        )
        return int(result.split()[-1]) if result else 0

    async def delete_many(self, collection: str, filter: dict) -> int:
        return await self.delete_one(collection, filter)

    async def count(self, collection: str, filter: dict = None) -> int:
        pool = await self._get_pool()
        if filter:
            where, params = self._build_where(filter)
            row = await pool.fetchrow(
                f'SELECT COUNT(*) FROM "{collection}" WHERE {where}', *params
            )
        else:
            row = await pool.fetchrow(f'SELECT COUNT(*) FROM "{collection}"')
        return row[0] if row else 0

    async def aggregate(self, collection: str, pipeline: list) -> list[dict]:
        # PostgreSQL 不直接支持 MongoDB 风格的 aggregation pipeline
        raise NotImplementedError("PostgreSQL 不支持 MongoDB 风格 aggregation pipeline")

    async def bulk_write(self, collection: str, operations: list[dict]) -> int:
        await self._ensure_table(collection)
        pool = await self._get_pool()
        import json
        count = 0
        async with pool.acquire() as conn:
            async with conn.transaction():
                for op in operations:
                    if op.get("action") == "upsert":
                        rid = op.get("_id", "")
                        data = op.get("data", {})
                        await conn.execute(
                            f'INSERT INTO "{collection}" (_id, data) VALUES ($1, $2) '
                            f'ON CONFLICT (_id) DO UPDATE SET data = $2, updated_at = NOW()',
                            rid, json.dumps(data, ensure_ascii=False)
                        )
                        count += 1
        return count

    # ---- 管理 ----
    async def health_check(self) -> dict:
        try:
            pool = await self._get_pool()
            row = await pool.fetchrow("SELECT version()")
            return {
                "backend": self.backend_name,
                "status": "healthy",
                "version": row[0] if row else "unknown",
                "pool_size": f"{self.min_size}-{self.max_size}",
            }
        except Exception as e:
            return {"backend": self.backend_name, "status": "unhealthy", "error": str(e)}

    async def create_index(self, collection: str, keys: list[tuple],
                           unique: bool = False) -> None:
        pool = await self._get_pool()
        idx_name = f"idx_{collection}_" + "_".join(k[0] for k in keys)
        unique_str = "UNIQUE" if unique else ""
        cols = ", ".join(f"((data->>'{k[0]}'))" for k in keys)
        await pool.execute(
            f'CREATE {unique_str} INDEX IF NOT EXISTS "{idx_name}" '
            f'ON "{collection}" ({cols})'
        )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    # ---- 工具 ----
    def _build_where(self, filter: dict) -> tuple[str, list]:
        """将 dict filter 转为 PostgreSQL WHERE 子句"""
        if not filter:
            return "TRUE", []
        conditions = []
        params = []
        for i, (k, v) in enumerate(filter.items()):
            param_name = f"${i+1}"
            if k == "_id":
                conditions.append(f"_id = {param_name}")
            else:
                conditions.append(f"data->>'{k}' = {param_name}")
            params.append(str(v))
        return " AND ".join(conditions), params

    @staticmethod
    def _row_to_dict(row) -> dict:
        """将 PostgreSQL row 转为 dict"""
        import json
        if not row:
            return {}
        result = {"_id": row["_id"],
                  "created_at": str(row["created_at"]),
                  "updated_at": str(row["updated_at"])}
        data = row["data"]
        if isinstance(data, str):
            data = json.loads(data)
        result.update(data)
        return result

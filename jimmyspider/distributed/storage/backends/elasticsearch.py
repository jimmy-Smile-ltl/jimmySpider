"""
Elasticsearch 储存后端

使用 elasticsearch-py 异步客户端（可选依赖 elasticsearch）。
"""

from typing import Optional

from ..base import StorageBackend


class ElasticsearchBackend(StorageBackend):
    backend_name = "elasticsearch"

    def __init__(self, hosts: list[str] = None,
                 index_prefix: str = "spider",
                 max_retries: int = 3):
        if hosts is None:
            hosts = ["http://localhost:9200"]
        self.hosts = hosts
        self.index_prefix = index_prefix
        self._client: Optional[object] = None

    async def _get_client(self):
        if self._client is None:
            from elasticsearch import AsyncElasticsearch
            self._client = AsyncElasticsearch(self.hosts, max_retries=3, retry_on_timeout=True)
        return self._client

    def _index_name(self, collection: str) -> str:
        return f"{self.index_prefix}_{collection}"

    async def insert_one(self, collection: str, record: dict, id_field: str = "_id") -> str:
        es = await self._get_client()
        rid = record.pop(id_field, record.get("_id", ""))
        result = await es.index(index=self._index_name(collection), id=rid, body=record)
        return result["_id"]

    async def insert_many(self, collection: str, records: list[dict], id_field: str = "_id") -> int:
        es = await self._get_client()
        import asyncio
        tasks = [self.insert_one(collection, r, id_field) for r in records]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, Exception))

    async def upsert(self, collection: str, record: dict, id_field: str = "_id") -> bool:
        await self.insert_one(collection, record, id_field)
        return True

    async def find_one(self, collection: str, filter: dict) -> Optional[dict]:
        es = await self._get_client()
        body = {"query": {"match": filter}} if filter else {"query": {"match_all": {}}}
        result = await es.search(index=self._index_name(collection), body=body, size=1)
        hits = result["hits"]["hits"]
        if hits:
            doc = hits[0]["_source"]
            doc["_id"] = hits[0]["_id"]
            return doc
        return None

    async def find_many(self, collection: str, filter: dict, sort: list = None,
                        skip: int = 0, limit: int = 100) -> list[dict]:
        es = await self._get_client()
        body = {"query": {"match": filter} if filter else {"match_all": {}}, "from": skip, "size": limit}
        if sort:
            body["sort"] = [{s[0]: {"order": s[1]}} for s in sort]
        result = await es.search(index=self._index_name(collection), body=body)
        docs = []
        for hit in result["hits"]["hits"]:
            doc = hit["_source"]
            doc["_id"] = hit["_id"]
            docs.append(doc)
        return docs

    async def update_one(self, collection: str, filter: dict, update: dict) -> int:
        es = await self._get_client()
        result = await es.update_by_query(
            index=self._index_name(collection),
            body={
                "query": {"match": filter},
                "script": {
                    "source": "ctx._source.putAll(params.update)",
                    "params": {"update": update}
                }
            })
        return result.get("updated", 0)

    async def update_many(self, collection: str, filter: dict, update: dict) -> int:
        return await self.update_one(collection, filter, update)

    async def delete_one(self, collection: str, filter: dict) -> int:
        es = await self._get_client()
        result = await es.delete_by_query(
            index=self._index_name(collection),
            body={"query": {"match": filter}})
        return result.get("deleted", 0)

    async def delete_many(self, collection: str, filter: dict) -> int:
        return await self.delete_one(collection, filter)

    async def count(self, collection: str, filter: dict = None) -> int:
        es = await self._get_client()
        body = {"query": {"match": filter}} if filter else {"query": {"match_all": {}}}
        result = await es.count(index=self._index_name(collection), body=body)
        return result["count"]

    async def aggregate(self, collection: str, pipeline: list) -> list[dict]:
        raise NotImplementedError

    async def bulk_write(self, collection: str, operations: list[dict]) -> int:
        es = await self._get_client()
        import asyncio
        tasks = []
        for op in operations:
            if op.get("action") == "upsert":
                rid = op.get("_id", "")
                tasks.append(es.index(index=self._index_name(collection), id=rid, body=op.get("data", {})))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, Exception))

    async def health_check(self) -> dict:
        try:
            es = await self._get_client()
            info = await es.info()
            return {"backend": self.backend_name, "status": "healthy",
                    "version": info.get("version", {}).get("number", "unknown")}
        except Exception as e:
            return {"backend": self.backend_name, "status": "unhealthy", "error": str(e)}

    async def create_index(self, collection: str, keys: list[tuple], unique: bool = False) -> None:
        pass  # ES 自动创建索引映射

    async def close(self) -> None:
        if self._client:
            await self._client.close()

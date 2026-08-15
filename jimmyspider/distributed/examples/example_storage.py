"""
分布式储存 — 使用示例

演示: MongoDB主库 + PostgreSQL备份库 的双写策略。

运行前提: 已安装 jimmySpider（或位于仓库根目录），数据库连接默认取全局配置
（MONGO_URI / PG_* / MYSQL_*），可显式传入覆盖。
运行方式: python -m jimmyspider.distributed.examples.example_storage
"""

import asyncio

from jimmyspider.distributed import DistributedStorageManager
from jimmyspider.distributed.storage.backends import (
    MongoDBBackend, PostgreSQLBackend, MySQLBackend, ElasticsearchBackend,
)


async def main():
    # ---- 创建管理器 (双写策略) ----
    mgr = DistributedStorageManager(strategy="dual_write")

    # 主储存: MongoDB（默认取全局配置 MONGO_URI / MONGO_DB）
    mgr.set_primary(MongoDBBackend(
        uri="mongodb://localhost:27017",
        database="spider",
        max_pool_size=50,
    ))

    # 备份储存: PostgreSQL（默认按全局配置 PG_* 拼接连接串）
    mgr.set_backup(PostgreSQLBackend(
        min_size=5, max_size=20,
    ))

    # ---- 写入测试数据 ----
    print("=== 写入测试 ===")
    reports = [
        {"_id": "001", "title": "研报A", "org": "机构A", "date": "2026-06-01"},
        {"_id": "002", "title": "研报B", "org": "机构B", "date": "2026-06-02"},
        {"_id": "003", "title": "研报C", "org": "机构C", "date": "2026-06-03"},
    ]

    for r in reports:
        rid = await mgr.insert_one("reports", r)
        print(f"  插入: {rid}")

    # 批量插入
    count = await mgr.insert_many("reports", reports)
    print(f"  批量插入: {count} 条")

    # ---- 查询 ----
    print("\n=== 查询 ===")
    result = await mgr.find_one("reports", {"_id": "001"})
    print(f"  单条: {result['title'] if result else 'N/A'}")

    results = await mgr.find_many("reports", {}, limit=10)
    print(f"  多条: {len(results)} 条")

    # ---- 更新 ----
    print("\n=== 更新 ===")
    updated = await mgr.update_one("reports", {"_id": "001"}, {"rating": "买入"})
    print(f"  更新: {updated} 条")

    # ---- 计数 ----
    total = await mgr.count("reports")
    print(f"\n=== 计数: {total} 条 ===")

    # ---- 健康检查 ----
    hc = await mgr.health_check()
    print(f"\n=== 健康检查: {hc['strategy']} ===")
    for name, status in hc.get("backends", {}).items():
        print(f"  {name}: {status.get('status', 'unknown')}")

    await mgr.close()


async def demo_read_write_split():
    """演示读写分离策略"""
    mgr = DistributedStorageManager(strategy="read_write_split")
    mgr.set_primary(MongoDBBackend(database="spider"))
    mgr.add_read_backend(MySQLBackend(database="spider"))

    await mgr.insert_one("test", {"_id": "rw1", "value": "test"})
    result = await mgr.find_one("test", {"_id": "rw1"})
    print(f"读写分离: {result['_id'] if result else 'N/A'}")

    await mgr.close()


async def demo_shard_by_collection():
    """演示按表分片策略"""
    mgr = DistributedStorageManager(strategy="shard_by_collection")
    mgr.set_primary(MongoDBBackend(database="spider"))
    mgr.shard_collection("logs", ElasticsearchBackend(index_prefix="spider_logs"))

    await mgr.insert_one("reports", {"_id": "s1", "data": "to MongoDB"})
    await mgr.insert_one("logs", {"_id": "s2", "data": "to Elasticsearch"})
    print("按表分片: reports→MongoDB, logs→ES")

    await mgr.close()


if __name__ == "__main__":
    asyncio.run(main())

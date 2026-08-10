"""
jimmySpider - PostgreSQL 存储处理器

支持自动重连、JSON 序列化、upsert（ON CONFLICT）、Schema 管理。
"""

import json
import time

import psycopg2
import psycopg2.extras

from jimmyspider.config import get_config


class PostgreSQLHandler:
    """PostgreSQL 数据库操作封装。

    使用方式:
        db = PostgreSQLHandler(table_name="my_table", schema="spider")
        db.insert_one({"id": 1, "title": "Hello"})
    """

    def __init__(
        self,
        table_name=None,
        db_name=None,
        return_type="tuple",
        schema="public",
    ):
        config = get_config()
        self.db_name = db_name or config.PG_DB
        self.table_name = table_name
        self.schema = schema or config.PG_SCHEMA
        self.return_type = return_type
        self.connection = self._get_connection(self.return_type)
        self._ensure_schema()
        if self.table_name:
            self._ensure_table()

    def _get_connection(self, return_type="tuple"):
        config = get_config()
        for i in range(5):
            try:
                cursor_factory = (
                    psycopg2.extras.RealDictCursor
                    if return_type == "dict"
                    else None
                )
                return psycopg2.connect(
                    host=config.PG_HOST,
                    port=config.PG_PORT,
                    user=config.PG_USER,
                    password=config.PG_PASSWORD,
                    dbname=self.db_name,
                    connect_timeout=30,
                    cursor_factory=cursor_factory,
                )
            except Exception as e:
                print(f"连接 PostgreSQL 失败: {e}, 重试 {i + 1}/5")
                time.sleep(min(30 * (i + 1), 120))
        raise Exception("无法连接到 PostgreSQL，请检查配置")

    def _ensure_schema(self):
        """确保 schema 存在"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE SCHEMA IF NOT EXISTS {self.schema}"
                )
                self.connection.commit()
        except Exception as e:
            print(f"创建 schema 失败: {e}")
            self.connection.rollback()

    def _ensure_table(self):
        """确保表存在"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""CREATE TABLE IF NOT EXISTS {self.schema}.{self.table_name} (
                        id BIGSERIAL PRIMARY KEY,
                        _unique_key VARCHAR(1024) UNIQUE,
                        data JSONB,
                        created_at TIMESTAMP DEFAULT NOW(),
                        updated_at TIMESTAMP DEFAULT NOW()
                    )"""
                )
                self.connection.commit()
        except Exception as e:
            print(f"创建表失败（可能已存在）: {e}")
            self.connection.rollback()

    def execute_query(self, query, params=None):
        """执行查询并返回结果"""
        for i in range(3):
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
            except psycopg2.Error as e:
                print(f"查询失败: {e}")
                self.connection = self._get_connection(self.return_type)
        return None

    def insert_one(self, data: dict) -> bool:
        """插入或更新单条数据。

        使用 data 中的 _unique_key 或 _id 作为唯一键做 upsert。
        完整数据存储在 JSONB 列中。
        """
        if not isinstance(data, dict):
            print("数据必须是字典格式")
            return False

        unique_val = data.get("_unique_key") or data.get(
            "_id"
        ) or data.get("url", "")
        json_data = json.dumps(data, ensure_ascii=False)

        sql = f"""INSERT INTO {self.schema}.{self.table_name} (_unique_key, data)
                  VALUES (%s, %s::jsonb)
                  ON CONFLICT (_unique_key) DO UPDATE SET
                      data = EXCLUDED.data,
                      updated_at = NOW()"""

        for i in range(3):
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql, (unique_val, json_data))
                    self.connection.commit()
                    return True
            except psycopg2.Error as e:
                print(f"插入失败: {e}")
                self.connection.rollback()
                self.connection = self._get_connection(self.return_type)
        return False

    def insert_many(self, data_list: list) -> int:
        """批量插入/更新。

        使用 executemany 高性能批量写入。返回成功数量。
        """
        if not data_list:
            return 0

        sql = f"""INSERT INTO {self.schema}.{self.table_name} (_unique_key, data)
                  VALUES (%s, %s::jsonb)
                  ON CONFLICT (_unique_key) DO UPDATE SET
                      data = EXCLUDED.data,
                      updated_at = NOW()"""

        count = 0
        batch_size = 100
        for start in range(0, len(data_list), batch_size):
            batch = data_list[start : start + batch_size]
            values = []
            for data in batch:
                unique_val = data.get("_unique_key") or data.get(
                    "_id"
                ) or data.get("url", "")
                json_data = json.dumps(data, ensure_ascii=False)
                values.append((unique_val, json_data))

            for i in range(3):
                try:
                    with self.connection.cursor() as cursor:
                        cursor.executemany(sql, values)
                        self.connection.commit()
                        count += len(values)
                        break
                except psycopg2.Error as e:
                    print(f"批量插入失败: {e}")
                    self.connection.rollback()
                    self.connection = self._get_connection(self.return_type)

        return count

    def count(self, where_clause="1=1") -> int:
        """计数"""
        result = self.execute_query(
            f"SELECT COUNT(*) FROM {self.schema}.{self.table_name} WHERE {where_clause}"
        )
        return result[0][0] if result else 0

    def close(self):
        """关闭连接"""
        try:
            self.connection.close()
        except Exception:
            pass

"""
jimmySpider - MySQL 存储处理器

支持自动重连、JSON 序列化、upsert（ON DUPLICATE KEY UPDATE）。
"""

import json
import time

import pymysql
import pymysql.cursors

from jimmyspider.config import get_config


class MySQLHandler:
    """MySQL 数据库操作封装。

    使用方式:
        db = MySQLHandler(db_name="jimmyspider", table_name="my_table")
        db.insert_data({"id": 1, "title": "Hello"})
    """

    def __init__(self, db_name=None, table_name=None, return_type="tuple"):
        config = get_config()
        self.db_name = db_name or config.MYSQL_DB
        self.table_name = table_name
        self.return_type = return_type
        self.connection = self._get_connection(self.return_type)
        if self.table_name:
            self._ensure_table()

    def _get_connection(self, return_type="tuple"):
        config = get_config()
        for i in range(5):
            try:
                cursorclass = (
                    pymysql.cursors.SSDictCursor
                    if return_type == "dict"
                    else pymysql.cursors.SSCursor
                )
                return pymysql.connect(
                    host=config.MYSQL_HOST,
                    port=config.MYSQL_PORT,
                    user=config.MYSQL_USER,
                    password=config.MYSQL_PASSWORD,
                    database=self.db_name,
                    connect_timeout=30,
                    autocommit=False,
                    cursorclass=cursorclass,
                )
            except Exception as e:
                print(f"连接 MySQL 失败: {e}, 重试 {i + 1}/5")
                time.sleep(min(30 * (i + 1), 120))
        raise Exception("无法连接到 MySQL，请检查配置")

    def _ensure_table(self):
        """确保表存在（如不存在则创建基本结构）"""
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""CREATE TABLE IF NOT EXISTS `{self.table_name}` (
                        `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
                        `_unique_key` VARCHAR(512) UNIQUE,
                        `data` JSON,
                        `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""
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
            except pymysql.Error as e:
                print(f"查询失败: {e}")
                self.connection = self._get_connection(self.return_type)
        return None

    def insert_one(self, data: dict, unique_col: str = "_unique_key") -> bool:
        """插入或更新单条数据。

        使用 _unique_key 字段做唯一键去重，数据序列化到 JSON 列。
        如果 data 中有 _unique_key 则使用它，否则用 _id 或 url 字段。
        """
        if not isinstance(data, dict):
            print("数据必须是字典格式")
            return False

        unique_val = data.get("_unique_key") or data.get(
            "_id"
        ) or data.get("url", "")
        json_data = json.dumps(data, ensure_ascii=False)

        sql = f"""INSERT INTO `{self.table_name}` (_unique_key, data)
                  VALUES (%s, %s)
                  ON DUPLICATE KEY UPDATE data = VALUES(data)"""

        for i in range(3):
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql, (unique_val, json_data))
                    self.connection.commit()
                    return True
            except pymysql.Error as e:
                print(f"插入失败: {e}")
                self.connection.rollback()
                self.connection = self._get_connection(self.return_type)
        return False

    def insert_many(self, data_list: list, unique_col: str = "_unique_key") -> int:
        """批量插入/更新。

        返回成功插入的数量。
        """
        if not data_list:
            return 0

        count = 0
        for i, item in enumerate(data_list):
            if self.insert_one(item, unique_col):
                count += 1
            if (i + 1) % 100 == 0:
                print(f"已处理 {i + 1}/{len(data_list)} 条")
        return count

    def count(self, where_clause="1=1") -> int:
        """计数"""
        result = self.execute_query(
            f"SELECT COUNT(*) FROM `{self.table_name}` WHERE {where_clause}"
        )
        return result[0][0] if result else 0

    def close(self):
        """关闭连接"""
        try:
            self.connection.close()
        except Exception:
            pass

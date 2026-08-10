"""
jimmySpider - MySQL 存储处理器

从北大信研院项目实战中提取，支持自动重连、批量插入、upsert、表管理。
"""

import json
import time
from collections import Counter

import pymysql
import pymysql.cursors

from jimmyspider.config import get_config


class MySQLHandler:
    """MySQL 数据库操作封装。

    insert_data 自动处理 dict/list 转为 JSON 字段；
    insert_data_list 先批量插入，失败则逐条 ON DUPLICATE KEY UPDATE。
    """

    def __init__(self, db_name, table_name, return_type="tuple"):
        self.db_name = db_name
        self.table_name = table_name
        self.return_type = return_type
        self.connection = self.get_db_connection(self.return_type)

    # ---- 连接 ----

    def get_db_connection(self, return_type="tuple"):
        config = get_config()
        for i in range(20):
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
                print(f"连接 MySQL 失败: {e}, 重试 {i + 1}/20")
                time.sleep(min(30 * (i + 1), 300))
                if i == 19:
                    raise Exception("无法连接到 MySQL，请检查配置")
        return None

    # ---- 查询 ----

    def execute_query(self, query, params=None):
        for i in range(5):
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
            except pymysql.Error as e:
                print(f"查询执行失败: {e}")
                self.connection = self.get_db_connection(self.return_type)
        return None

    # ---- 单条插入/更新 ----

    def insert_data(self, data, unique_col="article_url"):
        """插入单条。主键冲突时自动转为 ON DUPLICATE KEY UPDATE。"""
        if not isinstance(data, dict):
            print("数据必须是字典格式")
            return

        processed_data = data.copy()
        for key, value in processed_data.items():
            if isinstance(value, (list, dict)):
                processed_data[key] = json.dumps(value, ensure_ascii=False)

        columns = ", ".join([f"`{k}`" for k in processed_data.keys()])
        placeholders = ", ".join(["%s"] * len(processed_data))
        query = (
            f"INSERT INTO `{self.table_name}` ({columns}) VALUES ({placeholders})"
        )
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query, tuple(processed_data.values()))
                self.connection.commit()
                cursor.close()
                return "insert"
        except Exception:
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self.get_db_connection(self.return_type)
                self.connection.rollback()
            try:
                update_parts = [
                    f"`{k}` = VALUES(`{k}`)"
                    for k in processed_data
                    if k != unique_col
                ]
                update_clause = ", ".join(update_parts)
                query = (
                    f"INSERT INTO `{self.table_name}` ({columns}) VALUES ({placeholders}) "
                    f"ON DUPLICATE KEY UPDATE {update_clause}"
                )
                with self.connection.cursor() as cursor:
                    cursor.execute(query, tuple(processed_data.values()))
                    self.connection.commit()
                    cursor.close()
                    return "update"
            except Exception as e:
                print(
                    f"数据插入/更新均失败: {e} "
                    f"data={json.dumps(data, ensure_ascii=False)[:200]}"
                )
                try:
                    self.connection.rollback()
                except Exception:
                    self.connection = self.get_db_connection(self.return_type)
                    self.connection.rollback()

    # ---- 批量插入/更新 ----

    def insert_data_list(self, data_list, unique_col="article_url"):
        """批量插入。先用 executemany，失败则逐条 insert_data。"""
        if (
            not data_list
            or not isinstance(data_list, list)
            or not all(isinstance(d, dict) for d in data_list)
        ):
            print("数据必须是字典列表格式")
            return

        processed_list = []
        for data in data_list:
            processed_data = data.copy()
            for key, value in processed_data.items():
                if isinstance(value, (list, dict)):
                    processed_data[key] = json.dumps(value, ensure_ascii=False)
            processed_list.append(processed_data)
        if not processed_list:
            return

        sorted_keys = tuple(sorted(processed_list[0].keys()))
        columns = ", ".join([f"`{k}`" for k in sorted_keys])
        placeholders = ", ".join(["%s"] * len(sorted_keys))
        query = (
            f"INSERT INTO `{self.table_name}` ({columns}) VALUES ({placeholders})"
        )

        try:
            values = [tuple(d.get(k) for k in sorted_keys) for d in processed_list]
            with self.connection.cursor() as cursor:
                cursor.executemany(query, values)
                print(f"批量插入成功, 条数: {cursor.rowcount}")
                self.connection.commit()
                cursor.close()
            return "insert"
        except Exception as e:
            print(f"批量插入失败: {e}，改为逐个插入")
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self.get_db_connection(self.return_type)
                self.connection.rollback()

            operations = []
            for item in processed_list:
                op = self.insert_data(item, unique_col=unique_col)
                if op:
                    operations.append(op)
            counts = Counter(operations)
            print(f"总计 {len(processed_list)} 条，操作分布: {dict(counts)}")
            if len(operations) == 1:
                return operations[0]
            return "insert"

    # ---- 删除 ----

    def delete_condition_data(self, condition, max_num: int = 1000):
        if not isinstance(condition, dict) or not condition:
            print("条件必须是非空字典")
            return False
        if self._more_than(condition, max_num):
            user_input = input(f"数据超过 {max_num} 条，确认删除? (y/n): ")
            if user_input.lower() != "y":
                print("取消删除")
                return False
        where = " AND ".join([f"`{k}` = '{condition[k]}'" for k in condition])
        query = f"DELETE FROM `{self.table_name}` WHERE {where}"
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query)
                self.connection.commit()
                print(f"已删除 {cursor.rowcount} 条")
                cursor.close()
            return True
        except pymysql.Error as e:
            print(f"删除失败: {e}")
            return False

    # ---- 表管理 ----

    def create_table(self, create_sql):
        if "CREATE TABLE" not in create_sql:
            print("SQL 需包含 CREATE TABLE")
            return
        if self.table_name not in create_sql:
            print(f"SQL 中未包含表名 {self.table_name}")
            return
        if self.is_has_table(self.table_name):
            print(f"表 {self.table_name} 已存在")
            return
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(create_sql)
                self.connection.commit()
                cursor.close()
            print(f"表 {self.table_name} 创建成功")
        except pymysql.Error as e:
            print(f"创建表失败: {e}")
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self.get_db_connection(self.return_type)
                self.connection.rollback()

    def is_has_table(self, table_name):
        sql = """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
        """
        for _ in range(3):
            with self.connection.cursor() as cursor:
                cursor.execute(sql, (table_name,))
                row = cursor.fetchone()
                cursor.close()
                if isinstance(row, tuple):
                    return row[0] == 1
                if isinstance(row, dict) and row.get("COUNT(*)"):
                    return True
        return False

    def drop_table(self, max_num: int = 100):
        if not self.is_has_table(self.table_name):
            print(f"表 {self.table_name} 不存在")
            return False
        if self._more_than(max_num=max_num):
            print(f"表数据超过 {max_num} 条，默认不删除")
            return False
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS `{self.table_name}`")
                self.connection.commit()
                cursor.close()
            print(f"表 {self.table_name} 已删除")
            return True
        except pymysql.Error as e:
            print(f"删除表失败: {e}")
            return False

    def clear_table(self, max_num=100):
        if not self.is_has_table(self.table_name):
            print(f"表 {self.table_name} 不存在")
            return False
        if self._more_than(max_num=max_num):
            print(f"表数据超过 {max_num} 条，默认不清空")
            return False
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f"TRUNCATE TABLE `{self.table_name}`")
                self.connection.commit()
                cursor.close()
            print(f"表 {self.table_name} 已清空")
            return True
        except pymysql.Error as e:
            print(f"清空失败: {e}")
            return False

    # ---- 辅助 ----

    def _more_than(self, condition=None, max_num: int = 1000):
        if condition:
            where = " AND ".join([f"`{k}` = '{condition[k]}'" for k in condition])
            query = f"SELECT COUNT(*) FROM `{self.table_name}` WHERE {where}"
        else:
            query = f"SELECT COUNT(*) FROM `{self.table_name}`"
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query)
                count = cursor.fetchone()[0]
                print(f"表 {self.table_name} 当前 {count} 条", end="\t")
                return count > max_num
        except pymysql.Error as e:
            print(f"计数失败: {e}")
            return True  # 出错时宁可报多

    def getMinMaxId(self, table_name=None):
        tbl = table_name or self.table_name
        query = f"SELECT MIN(id) AS min_id, MAX(id) AS max_id FROM `{self.db_name}`.`{tbl}`"
        try:
            with self.connection.cursor(pymysql.cursors.DictCursor) as cursor:
                cursor.execute(query)
                result = cursor.fetchone()
                if result:
                    return result.get("min_id"), result.get("max_id")
                return None, None
        except pymysql.Error as e:
            print(f"查询 min/max id 失败: {e}")
            return None, None

    def close(self):
        if self.connection:
            self.connection.close()
        print("MySQL 连接已关闭")

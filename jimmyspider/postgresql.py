"""
jimmySpider - PostgreSQL 存储处理器

从盛大网络项目实战中提取，支持自动重连、批量 upsert（RETURNING xmax）、
Schema 管理、逐条降级策略、优雅的 UniqueViolation 诊断。
"""

import json
import re
import time
from collections import Counter

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from jimmyspider.config import get_config


def parse_unique_violation(e: psycopg2.errors.UniqueViolation):
    """解析 UniqueViolation 异常，返回 (冲突列名, 友好提示)。"""
    error_msg = str(e)
    constraint_match = re.search(
        r'violates unique constraint "([^"]+)"', error_msg
    )
    constraint_name = constraint_match.group(1) if constraint_match else "未知约束"
    detail_match = re.search(r"Key \(([^)]+)\)=\(([^)]+)\)", error_msg)
    key_column = detail_match.group(1) if detail_match else "未知列"
    key_value = detail_match.group(2) if detail_match else "未知值"

    if constraint_name.endswith("_pkey"):
        table_name_guess = constraint_name.replace("_pkey", "")
        return (
            key_column,
            f"【主键冲突】表 {table_name_guess}, 列 '{key_column}' 值 '{key_value}' 已存在。\n"
            f"  => 执行 SELECT setval(pg_get_serial_sequence('{table_name_guess}', '{key_column}'), "
            f"(SELECT MAX({key_column}) FROM {table_name_guess})); 重置序列",
        )
    else:
        return (
            key_column,
            f"【唯一约束冲突】约束 {constraint_name}, 列 '{key_column}' 值 '{key_value}' 已存在。\n"
            f"  => 检查数据源去重，或确保 ON CONFLICT 针对正确的唯一键",
        )


class PostgreSQLHandler:
    """PostgreSQL 数据库操作封装。

    核心能力：
    - insert_data: 单条，IntegrityError 时自动 UPDATE
    - insert_data_list: 批量，ON CONFLICT ... RETURNING (xmax=0) AS inserted
      失败则自动降级为逐条 insert_data
    - 完整表管理：create/drop/clear + Schema 自动创建
    """

    def __init__(
        self,
        table_name,
        db_name="postgres",
        return_type="tuple",
        schema="spider",
    ):
        config = get_config()
        self.db_name = db_name or config.PG_DB
        self.table_name = table_name
        self.schema = schema or config.PG_SCHEMA
        self.return_type = return_type
        self.connection = self._get_connection(self.return_type)
        self.create_schema_if_not_exists(self.schema)
        self.has_print_friendly_message = False
        self.key_cols = []

    # ---- 连接 ----

    def _get_connection(self, return_type="tuple"):
        config = get_config()
        for i in range(20):
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
                print(f"连接 PostgreSQL 失败: {e}, 重试 {i + 1}/20")
                time.sleep(min(60 * (i + 1), 600))
                if i == 19:
                    raise Exception("无法连接到 PostgreSQL，请检查配置")
        return None

    # ---- Schema 管理 ----

    def schema_exists(self, schema_name: str = None) -> bool:
        name = schema_name or self.schema
        query = "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s"
        try:
            with self.connection.cursor() as cur:
                cur.execute(query, (name,))
                return cur.fetchone() is not None
        except psycopg2.Error as e:
            print(f"检查 schema 失败: {e}")
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self._get_connection(self.return_type)
            return False

    def create_schema_if_not_exists(self, schema_name: str = None, owner: str = None):
        name = schema_name or self.schema
        if self.schema_exists(name):
            return True
        try:
            with self.connection.cursor() as cur:
                if owner:
                    stmt = sql.SQL(
                        "CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}"
                    ).format(sql.Identifier(name), sql.Identifier(owner))
                else:
                    stmt = sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(name)
                    )
                cur.execute(stmt)
            self.connection.commit()
            return True
        except psycopg2.Error as e:
            print(f"创建 schema 失败: {e}")
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self._get_connection(self.return_type)
            return False

    # ---- SQL 执行 ----

    def execute(self, sql_str: str):
        for _ in range(3):
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql_str)
                    self.connection.commit()
                    return True
            except psycopg2.Error as e:
                print(f"执行失败: {e}")
                if "already exists" in str(e):
                    self.connection.rollback()
                    return True
                self.connection = self._get_connection(self.return_type)
        return False

    def execute_query(self, query, params=None):
        for _ in range(5):
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(query, params)
                    return cursor.fetchall()
            except psycopg2.Error as e:
                print(f"查询失败: {e}")
                self.connection = self._get_connection(self.return_type)
        return None

    # ---- 单条插入/更新 ----

    def insert_data(self, data, unique_col=None, table_name=None):
        if not isinstance(data, dict):
            print("数据必须是字典格式")
            return

        tbl = table_name or self.table_name
        processed = data.copy()
        for k, v in processed.items():
            if isinstance(v, (list, dict)):
                processed[k] = json.dumps(v, ensure_ascii=False)

        cols = ", ".join([f'"{k}"' for k in processed])
        ph = ", ".join([f"%({k})s" for k in processed])
        insert_sql = (
            f'INSERT INTO "{self.schema}"."{tbl}" ({cols}) VALUES ({ph})'
        )

        try:
            with self.connection.cursor() as cursor:
                cursor.execute(insert_sql, processed)
                self.connection.commit()
                return "insert"
        except psycopg2.IntegrityError as e:
            if "duplicate key value violates unique constraint" not in str(e):
                print(f"插入失败（非重复键）: {e}")
                self.connection.rollback()
                return None
            try:
                self.connection.rollback()
                if self.table_name != "source_collection_info":
                    # 排除 id 和 unique_col
                    excluded = {"id"}
                    if self.key_cols:
                        excluded.update(self.key_cols)
                    elif unique_col:
                        if isinstance(unique_col, str):
                            excluded.add(unique_col)
                        elif isinstance(unique_col, (list, set)):
                            excluded.update(unique_col)

                    update_clause = ", ".join([
                        f'"{k}" = %({k})s'
                        for k in processed
                        if k not in excluded
                    ])
                    if update_clause:
                        if self.key_cols:
                            where = " AND ".join([
                                f'"{c}" = %({c})s' for c in self.key_cols
                            ])
                        elif isinstance(unique_col, str):
                            where = f'"{unique_col}" = %({unique_col})s'
                        elif isinstance(unique_col, (list, set)):
                            where = " AND ".join([
                                f'"{c}" = %({c})s' for c in unique_col
                            ])
                        else:
                            where = "1=1"
                        update_sql = (
                            f'UPDATE "{self.schema}"."{tbl}" '
                            f"SET {update_clause} WHERE {where}"
                        )
                        with self.connection.cursor() as cursor:
                            cursor.execute(update_sql, processed)
                            self.connection.commit()
                    else:
                        print("没有需要更新的字段")
                return "update"
            except Exception as update_e:
                print(f"更新失败: {update_e}")
                try:
                    self.connection.rollback()
                    if not self.has_print_friendly_message:
                        key_col, msg = parse_unique_violation(e)
                        if key_col and key_col != "未知列":
                            self.key_cols = [
                                k.strip() for k in key_col.split(",")
                            ]
                        print(f"\n--- 数据库操作提示 ---\n{msg}\n---\n")
                        self.has_print_friendly_message = True
                except Exception:
                    self.connection = self._get_connection(self.return_type)
                return None
        except Exception as e:
            print(f"插入失败: {e}")
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self._get_connection(self.return_type)
            return None

    # ---- 批量插入/更新 ----

    def insert_data_list(self, data_list, unique_col=None, table_name=None):
        """批量 upsert。使用 RETURNING (xmax=0) AS inserted 区分插入/更新。
        失败时自动降级为逐条 insert_data。"""
        if (
            not data_list
            or not isinstance(data_list, list)
            or not all(isinstance(d, dict) for d in data_list)
        ):
            print("数据必须是字典列表格式")
            return

        tbl = table_name or self.table_name
        processed = []
        for data in data_list:
            p = data.copy()
            for k, v in p.items():
                if isinstance(v, (list, dict)):
                    p[k] = json.dumps(v, ensure_ascii=False)
            processed.append(p)
        if not processed:
            return

        keys = processed[0].keys()
        cols_id = [sql.Identifier(k) for k in keys]

        if unique_col:
            if isinstance(unique_col, str):
                excluded = {"id", unique_col}
                conflict_cols = sql.Identifier(unique_col)
            elif isinstance(unique_col, (list, set)):
                excluded = set(unique_col) | {"id"}
                conflict_cols = sql.SQL(", ").join(
                    map(sql.Identifier, unique_col)
                )
            else:
                raise ValueError("unique_col 应为 str/list/set")

            update_parts = [
                sql.SQL("{} = EXCLUDED.{}").format(
                    sql.Identifier(k), sql.Identifier(k)
                )
                for k in keys
                if k not in excluded
            ]
            query = sql.SQL(
                "INSERT INTO {schema}.{tbl} ({columns}) VALUES %s "
                "ON CONFLICT ({conflict}) DO UPDATE SET {updates} "
                "RETURNING (xmax = 0) AS inserted"
            ).format(
                schema=sql.Identifier(self.schema),
                tbl=sql.Identifier(tbl),
                columns=sql.SQL(", ").join(cols_id),
                conflict=conflict_cols,
                updates=sql.SQL(", ").join(update_parts),
            )
        else:
            query = sql.SQL(
                "INSERT INTO {schema}.{tbl} ({columns}) VALUES %s "
                "RETURNING (xmax = 0) AS inserted"
            ).format(
                schema=sql.Identifier(self.schema),
                tbl=sql.Identifier(tbl),
                columns=sql.SQL(", ").join(cols_id),
            )

        tuples = [tuple(d.get(k) for k in keys) for d in processed]

        try:
            with self.connection.cursor() as cursor:
                results = psycopg2.extras.execute_values(
                    cursor, query.as_string(cursor), tuples,
                    template=None, fetch=True,
                )
                if results and isinstance(results[0], (dict, psycopg2.extras.RealDictRow)):
                    inserted = sum(1 for r in results if r.get("inserted", False))
                else:
                    inserted = sum(1 for row in results if row[0])
                updated = len(results) - inserted
                self.connection.commit()
                info = (
                    f"批量 upsert 成功 [{tbl}]: "
                    f"输入 {len(processed)} 条, "
                    f"插入 {inserted} 条, 更新 {updated} 条"
                )
                return "success: " + info
        except psycopg2.errors.UniqueViolation as e:
            if not self.has_print_friendly_message:
                key_col, msg = parse_unique_violation(e)
                if key_col and key_col != "未知列":
                    self.key_cols = [k.strip() for k in key_col.split(",")]
                print(f"\n--- 数据库操作提示 ---\n{msg}\n---\n")
                self.has_print_friendly_message = True
            self.connection.rollback()
            results = [
                self.insert_data(d, unique_col=unique_col, table_name=tbl)
                for d in processed
            ]
            counts = Counter(results)
            return f"unique_violation, 逐条结果: {dict(counts)}"
        except Exception as e:
            print(f"批量 upsert 失败, 改为逐条: {e}")
            self.connection.rollback()
            results = [
                self.insert_data(d, unique_col=unique_col, table_name=tbl)
                for d in processed
            ]
            counts = Counter(results)
            return f"逐条结果: {dict(counts)}"

    # ---- 更新 ----

    def update_data(self, data: dict, condition: dict, table_name: str = None):
        if not data or not condition:
            return False
        for k, v in data.items():
            if isinstance(v, (list, dict)):
                data[k] = json.dumps(v, ensure_ascii=False)
        tbl = table_name or self.table_name
        set_clause = ", ".join([f'"{k}" = %s' for k in data])
        where_clause = " AND ".join([f'"{k}" = %s' for k in condition])
        sql_str = (
            f'UPDATE "{self.schema}"."{tbl}" SET {set_clause} WHERE {where_clause}'
        )
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    sql_str, tuple(data.values()) + tuple(condition.values())
                )
                self.connection.commit()
                return True
        except psycopg2.Error as e:
            print(f"更新失败: {e}")
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self._get_connection(self.return_type)
            return False

    def update_data_list(
        self, data_list: list, condition_cols: list, table_name: str = None
    ):
        if not data_list or not condition_cols:
            return False
        for data in data_list:
            for k, v in data.items():
                if isinstance(v, (list, dict)):
                    data[k] = json.dumps(v, ensure_ascii=False)
        tbl = table_name or self.table_name
        first = data_list[0]
        set_keys = [k for k in first if k not in condition_cols]
        if not set_keys:
            return False
        set_clause = ", ".join([f'"{k}" = %s' for k in set_keys])
        where_clause = " AND ".join([f'"{k}" = %s' for k in condition_cols])
        update_sql = (
            f'UPDATE "{self.schema}"."{tbl}" SET {set_clause} WHERE {where_clause}'
        )
        params = []
        for data in data_list:
            params.append(
                tuple(data.get(k) for k in set_keys)
                + tuple(data.get(k) for k in condition_cols)
            )
        try:
            with self.connection.cursor() as cursor:
                psycopg2.extras.execute_batch(
                    cursor, update_sql, params, page_size=100
                )
                self.connection.commit()
                print(f"批量更新 {len(params)} 条成功")
                return True
        except psycopg2.Error as e:
            print(f"批量更新失败，回滚后逐条: {e}")
            self.connection.rollback()
            with self.connection.cursor() as cursor:
                for p in params:
                    try:
                        cursor.execute(update_sql, p)
                    except psycopg2.Error:
                        pass
                self.connection.commit()
            return True

    # ---- 删除 ----

    def delete_condition_data(self, condition, max_num: int = 1000):
        if not isinstance(condition, dict) or not condition:
            return False
        if self._more_than(condition, max_num):
            ans = input(f"数据超过 {max_num} 条，确认删除? (y/n): ")
            if ans.lower() != "y":
                print("取消删除")
                return False
        where = " AND ".join([f'"{k}" = %s' for k in condition])
        query = (
            f'DELETE FROM "{self.schema}"."{self.table_name}" WHERE {where}'
        )
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query, tuple(condition.values()))
                self.connection.commit()
                print(f"已删除 {cursor.rowcount} 条")
            return True
        except psycopg2.Error as e:
            print(f"删除失败: {e}")
            return False

    # ---- 表管理 ----

    def create_table(self, create_sql):
        if "CREATE TABLE" not in create_sql:
            return
        if self.table_name not in create_sql:
            print(f"SQL 中未包含表名 {self.table_name}")
            return
        # 自动补 schema
        if "spider." not in create_sql and f'"{self.schema}".' not in create_sql:
            m = re.search(
                r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([^\s(]+)",
                create_sql,
                re.IGNORECASE,
            )
            if m:
                old = m.group(1)
                pure = old.replace('"', "").split(".", 1)[-1]
                new = f'"{self.schema}"."{pure}"'
                create_sql = create_sql.replace(old, new, 1)
        if self.is_has_table(self.table_name):
            print(f"表 {self.table_name} 已存在")
            return
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(create_sql)
                self.connection.commit()
            print(f"表 {self.table_name} 创建成功")
        except psycopg2.Error as e:
            print(f"创建表失败: {e}")
            try:
                self.connection.rollback()
            except Exception:
                self.connection = self._get_connection(self.return_type)

    def is_has_table(self, table_name, schema=None):
        schema = schema or self.schema
        sql_str = """
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = %s AND table_name = %s
        """
        for _ in range(3):
            try:
                with self.connection.cursor() as cursor:
                    cursor.execute(sql_str, (schema, table_name))
                    row = cursor.fetchone()
                    if row is not None:
                        return (
                            row.get("count", 0) == 1
                            if isinstance(row, dict)
                            else row[0] == 1
                        )
            except psycopg2.Error as e:
                print(f"检查表存在失败: {e}")
                if "current transaction is aborted" in str(e):
                    self.connection.rollback()
                else:
                    self.connection = self._get_connection(self.return_type)
                time.sleep(1)
        print(f"无法确定表 '{table_name}' 是否存在")
        return False

    def drop_table(self, table_name=None, max_num: int = 100):
        tbl = table_name or self.table_name
        query = f'DROP TABLE IF EXISTS "{self.schema}"."{tbl}"'
        if not self.is_has_table(tbl):
            return True
        if self._more_than(max_num=max_num, table_name=tbl):
            print(f"表数据超过 {max_num} 条，不删除")
            return False
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query)
                self.connection.commit()
            print(f"表 {tbl} 已删除")
            return True
        except psycopg2.Error as e:
            print(f"删除表失败: {e}")
            return False

    def clear_table(self, max_num=100, table_name=None):
        tbl = table_name or self.table_name
        if not self.is_has_table(tbl):
            return False
        if self._more_than(max_num=max_num, table_name=tbl):
            print(f"表数据超过 {max_num} 条，不清空")
            return False
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(f'TRUNCATE TABLE "{self.schema}"."{tbl}"')
                self.connection.commit()
            print(f"表 {tbl} 已清空")
            return True
        except psycopg2.Error as e:
            print(f"清空失败: {e}")
            return False

    # ---- JSON 写入 ----

    def write_to_json_line(self, data: dict, table_name=None):
        """追加单条 JSON 到文件（JSONL 格式）"""
        tbl = table_name or self.table_name
        with open(f"{tbl}.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def write_to_json_lines(self, data_list: list, table_name=None):
        """批量追加 JSON 到文件"""
        tbl = table_name or self.table_name
        count = 0
        with open(f"{tbl}.json", "a", encoding="utf-8") as f:
            for data in data_list:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
                count += 1
        print(f"已写入 {count} 条到 {tbl}.json")

    # ---- 辅助 ----

    def _more_than(self, condition=None, max_num=1000, table_name=None):
        tbl = table_name or self.table_name
        if condition:
            where = " AND ".join([f'"{k}" = %s' for k in condition])
            query = f'SELECT COUNT(*) as all_num FROM "{self.schema}"."{tbl}" WHERE {where}'
            params = tuple(condition.values())
        else:
            query = f'SELECT COUNT(*) as all_num FROM "{self.schema}"."{tbl}"'
            params = None
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(query, params)
                row = cursor.fetchone()
                count = row.get("all_num", 0) if isinstance(row, dict) else row[0]
                print(f"表 {tbl} 当前 {count} 条", end="\t")
                return count > max_num
        except psycopg2.Error as e:
            print(f"计数失败: {e}")
            return True

    def getMinMaxId(self, table_name=None):
        tbl = table_name or self.table_name
        query = f'SELECT MIN(id) AS min_id, MAX(id) AS max_id FROM "{self.schema}"."{tbl}"'
        try:
            with self.connection.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cursor:
                cursor.execute(query)
                row = cursor.fetchone()
                if row:
                    return row.get("min_id"), row.get("max_id")
                return None, None
        except psycopg2.Error as e:
            print(f"查询 min/max id 失败: {e}")
            return None, None

    def getTotalCount(self, table_name=None):
        tbl = table_name or self.table_name
        query = f'SELECT count(*) AS total_count FROM "{self.schema}"."{tbl}"'
        try:
            with self.connection.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cursor:
                cursor.execute(query)
                row = cursor.fetchone()
                return row.get("total_count") if row else None
        except psycopg2.Error as e:
            print(f"查询总数失败: {e}")
            return None

    def close(self):
        if self.connection:
            self.connection.close()
        print("PostgreSQL 连接已关闭")

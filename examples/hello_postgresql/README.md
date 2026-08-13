# hello_postgresql — PostgreSQL 数据库入门示例

与 `hello_world` 完全相同的爬虫逻辑，唯一区别是数据库后端切换为 PostgreSQL。

## 展示特性

- 基类 `db_type="postgresql"` 数据库切换
- `save_result()` 自动路由到 `PostgreSQLHandler.insert_data_list`
- 批量 upsert 使用 `ON CONFLICT ... RETURNING (xmax=0) AS inserted` 精准区分插入/更新
- Redis 断点续爬 + `extractSoup` HTML 解析

## 前置条件

```bash
pip install psycopg2-binary
# 创建数据库（默认 jimmyspider，可在 jimmyspider.yaml 修改 pg_db）
createdb jimmyspider
```

## 运行

```bash
python spider.py
```

## 数据表结构

PostgreSQLHandler 自动建表于 `public` schema（可配置 `pg_schema`）：
`_unique_key`（唯一键）+ `data`（JSONB）+ 时间戳。重复运行自动 upsert。

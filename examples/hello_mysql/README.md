# hello_mysql — MySQL 数据库入门示例

与 `hello_world` 完全相同的爬虫逻辑，唯一区别是数据库后端切换为 MySQL。

## 展示特性

- 基类 `db_type="mysql"` 数据库切换
- `save_result()` 自动路由到 `MySQLHandler.insert_data_list`
- Redis 断点续爬（页码缓存 + 完成标记）
- `extractSoup` HTML 解析

## 前置条件

```bash
pip install pymysql
# 创建数据库（默认 jimmyspider，可在 jimmyspider.yaml 修改 mysql_db）
mysql -u root -e "CREATE DATABASE IF NOT EXISTS jimmyspider"
```

## 运行

```bash
python spider.py
```

## 数据表结构

MySQLHandler 自动建表：`_unique_key`（URL MD5，唯一键）+ `data`（JSON）+ 时间戳。
重复运行自动 upsert，不会产生重复数据。

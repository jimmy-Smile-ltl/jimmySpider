# 配置指南

jimmySpider 支持三层配置，**优先级从高到低**：

1. **环境变量**（最高优先级，覆盖其他所有配置）
2. **YAML 配置文件**（推荐，方便集中管理）
3. **默认值**（开箱即用）

## 推荐方式：YAML 配置文件

最简单的方式是使用 YAML 配置文件，所有设置集中在一个文件里。

### 1. 创建配置文件

```bash
# 复制模板文件
cp jimmyspider.yaml.example jimmyspider.yaml
```

### 2. 编辑配置

```yaml
# jimmyspider.yaml
mongo_uri: "mongodb://localhost:27017/"
mongo_db: "my_spider_db"

redis_host: "127.0.0.1"
redis_port: 6379

data_dir: "/data/spider_files"

# 代理（三选一）
proxy_tunnel_url: "http://user:pass@proxy.example.com:15818"

# Clash 代理池
clash_api_url: "http://127.0.0.1:9097"
clash_secret: "my-secret"
```

### 3. 放置位置

配置文件查找顺序（找到第一个即停止）：

1. `JIMMYSPIDER_CONFIG_FILE` 环境变量指定的路径
2. 当前工作目录下的 `jimmyspider.yaml`
3. 当前工作目录下的 `jimmyspider.yml`
4. 用户目录下的 `~/.jimmyspider.yaml`（全局用户配置）

### 4. 环境变量覆盖

环境变量始终优先。适合在 Docker/CI 中覆盖特定配置：

```bash
# .env 或 Dockerfile 中覆盖数据库地址
export JIMMYSPIDER_MONGO_URI="mongodb://prod-host:27017/"
```

## 全部配置项

### YAML 方式
export JIMMYSPIDER_REDIS_PASSWORD=""
export JIMMYSPIDER_DATA_DIR="/data/spider_files"
```

## 全部环境变量

### MongoDB

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_MONGO_URI` | `mongodb://localhost:27017/` | MongoDB 连接字符串 |
| `JIMMYSPIDER_MONGO_DB` | `jimmyspider` | 数据库名 |

```bash
# 带认证的 MongoDB
export JIMMYSPIDER_MONGO_URI="mongodb://user:pass@host:27017/"
# 副本集
export JIMMYSPIDER_MONGO_URI="mongodb://host1:27017,host2:27017/?replicaSet=rs0"
```

### Redis

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_REDIS_HOST` | `127.0.0.1` | Redis 地址 |
| `JIMMYSPIDER_REDIS_PORT` | `6379` | Redis 端口 |
| `JIMMYSPIDER_REDIS_PASSWORD` | `None` | Redis 密码 |
| `JIMMYSPIDER_REDIS_DB` | `0` | Redis 数据库编号 |

```bash
# 带密码的 Redis
export JIMMYSPIDER_REDIS_PASSWORD="my_redis_password"
# 使用不同数据库
export JIMMYSPIDER_REDIS_DB="1"
```

### 文件存储

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_DATA_DIR` | `~/spider_files` | 文件下载根目录 |

```
{JIMMYSPIDER_DATA_DIR}/
├── my_project/
│   ├── files_by_date/
│   │   └── 2026-08-10/
│   │       └── report.pdf
│   └── html_by_date/
│       └── 2026-08-10/
│           └── page_1.html
├── another_project/
│   └── ...
```

### 代理

#### 隧道代理（简单）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_PROXY_TUNNEL_URL` | `None` | 隧道代理 URL |

```bash
# 快代理隧道
export JIMMYSPIDER_PROXY_TUNNEL_URL="http://user:pass@proxy.kdlapi.com:15818"

# 其他隧道代理
export JIMMYSPIDER_PROXY_TUNNEL_URL="http://user:pass@proxy.example.com:8080"
```

#### 快代理 API 动态获取（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_PROXY_API_URL` | `None` | 快代理 API 完整 URL |

```bash
export JIMMYSPIDER_PROXY_API_URL="https://tps.kdlapi.com/api/gettps/?secret_id=xxx&signature=xxx&num=1&format=json&sep=1"
```

#### Clash 代理池

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_CLASH_API_URL` | `http://127.0.0.1:9097` | Clash External Controller 地址 |
| `JIMMYSPIDER_CLASH_SECRET` | `""` | Clash API 密钥 |
| `JIMMYSPIDER_CLASH_PROXY_URL` | `http://127.0.0.1:7897` | Clash 代理端口 |
| `JIMMYSPIDER_CLASH_POLICY_GROUP` | `自动选择` | 代理策略组名 |

```bash
export JIMMYSPIDER_CLASH_API_URL="http://127.0.0.1:9097"
export JIMMYSPIDER_CLASH_SECRET="my_clash_secret"
export JIMMYSPIDER_CLASH_PROXY_URL="http://127.0.0.1:7897"
export JIMMYSPIDER_CLASH_POLICY_GROUP="🚀 节点选择"
```

### 日志

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_LOG_DIR` | `None` | 日志目录（默认项目下的 `logs/`） |

### SSL/TLS

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `JIMMYSPIDER_SSL_CERT_FILE` | `None` | 自定义 CA 证书路径（默认用 certifi） |

## 在 Python 中读取配置

```python
from jimmyspider.config import get_config

config = get_config()
print(config.MONGO_URI)
print(config.REDIS_HOST)
print(config.DATA_DIR)
```

## 使用 .env 文件

推荐在项目根目录创建 `.env` 文件（已被 `.gitignore` 排除）：

```bash
# .env
JIMMYSPIDER_MONGO_URI=mongodb://localhost:27017/
JIMMYSPIDER_MONGO_DB=jimmyspider
JIMMYSPIDER_REDIS_HOST=127.0.0.1
JIMMYSPIDER_DATA_DIR=./spider_files
```

然后使用 `python-dotenv` 加载：

```python
# 在爬虫入口最顶部
from dotenv import load_dotenv
load_dotenv()

# 之后正常导入 jimmyspider
from jimmyspider import JimmySpider
```

## Docker 环境

```dockerfile
FROM python:3.11

ENV JIMMYSPIDER_MONGO_URI=mongodb://mongo:27017/
ENV JIMMYSPIDER_REDIS_HOST=redis
ENV JIMMYSPIDER_DATA_DIR=/data/spider_files

RUN pip install jimmyspider
```

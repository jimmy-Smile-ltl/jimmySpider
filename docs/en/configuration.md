# Configuration Guide

jimmySpider supports three levels of configuration, **from highest to lowest priority**:

1. **Environment variables** (highest priority; override all other configuration)
2. **YAML config file** (recommended; convenient for centralized management)
3. **Defaults** (works out of the box)

## Recommended Approach: YAML Config File

The simplest way is to use a YAML config file so all settings live in one place.

### 1. Create the config file

```bash
# Copy the template
cp jimmyspider.yaml.example jimmyspider.yaml
```

### 2. Edit the config

```yaml
# jimmyspider.yaml
mongo_uri: "mongodb://localhost:27017/"
mongo_db: "my_spider_db"

redis_host: "127.0.0.1"
redis_port: 6379

data_dir: "/data/spider_files"

# Proxy (pick one of three)
proxy_tunnel_url: "http://user:pass@proxy.example.com:15818"

# Clash proxy pool
clash_api_url: "http://127.0.0.1:9097"
clash_secret: "my-secret"
```

### 3. Where to place it

The config file is looked up in the following order (stops at the first match):

1. The path specified by the `JIMMYSPIDER_CONFIG_FILE` environment variable
2. `jimmyspider.yaml` in the current working directory
3. `jimmyspider.yml` in the current working directory
4. `~/.jimmyspider.yaml` in the user home directory (global user config)

### 4. Environment variable overrides

Environment variables always take precedence. Useful for overriding specific settings in Docker/CI:

```bash
# Override the database address in .env or the Dockerfile
export JIMMYSPIDER_MONGO_URI="mongodb://prod-host:27017/"
```

## All Configuration Items

### Via YAML

```bash
export JIMMYSPIDER_REDIS_PASSWORD=""
export JIMMYSPIDER_DATA_DIR="/data/spider_files"
```

## All Environment Variables

### MongoDB

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_MONGO_URI` | `mongodb://localhost:27017/` | MongoDB connection string |
| `JIMMYSPIDER_MONGO_DB` | `jimmyspider` | Database name |

```bash
# MongoDB with authentication
export JIMMYSPIDER_MONGO_URI="mongodb://user:pass@host:27017/"
# Replica set
export JIMMYSPIDER_MONGO_URI="mongodb://host1:27017,host2:27017/?replicaSet=rs0"
```

### Redis

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_REDIS_HOST` | `127.0.0.1` | Redis host |
| `JIMMYSPIDER_REDIS_PORT` | `6379` | Redis port |
| `JIMMYSPIDER_REDIS_PASSWORD` | `None` | Redis password |
| `JIMMYSPIDER_REDIS_DB` | `0` | Redis database number |

```bash
# Redis with a password
export JIMMYSPIDER_REDIS_PASSWORD="my_redis_password"
# Use a different database
export JIMMYSPIDER_REDIS_DB="1"
```

### File Storage

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_DATA_DIR` | `~/spider_files` | Root directory for file downloads |

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

### Proxy

#### Tunnel Proxy (Simple)

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_PROXY_TUNNEL_URL` | `None` | Tunnel proxy URL |

```bash
# Kuaidaili tunnel
export JIMMYSPIDER_PROXY_TUNNEL_URL="http://user:pass@proxy.kdlapi.com:15818"

# Other tunnel proxy
export JIMMYSPIDER_PROXY_TUNNEL_URL="http://user:pass@proxy.example.com:8080"
```

#### Kuaidaili API Dynamic Fetch (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_PROXY_API_URL` | `None` | Full Kuaidaili API URL |

```bash
export JIMMYSPIDER_PROXY_API_URL="https://tps.kdlapi.com/api/gettps/?secret_id=xxx&signature=xxx&num=1&format=json&sep=1"
```

#### Clash Proxy Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_CLASH_API_URL` | `http://127.0.0.1:9097` | Clash External Controller address |
| `JIMMYSPIDER_CLASH_SECRET` | `""` | Clash API secret |
| `JIMMYSPIDER_CLASH_PROXY_URL` | `http://127.0.0.1:7897` | Clash proxy port |
| `JIMMYSPIDER_CLASH_POLICY_GROUP` | `自动选择` | Proxy policy group name |

```bash
export JIMMYSPIDER_CLASH_API_URL="http://127.0.0.1:9097"
export JIMMYSPIDER_CLASH_SECRET="my_clash_secret"
export JIMMYSPIDER_CLASH_PROXY_URL="http://127.0.0.1:7897"
export JIMMYSPIDER_CLASH_POLICY_GROUP="🚀 节点选择"
```

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_LOG_DIR` | `None` | Log directory (defaults to `logs/` under the project) |

### SSL/TLS

| Variable | Default | Description |
|----------|---------|-------------|
| `JIMMYSPIDER_SSL_CERT_FILE` | `None` | Custom CA certificate path (defaults to certifi) |

## Reading Config in Python

```python
from jimmyspider.config import get_config

config = get_config()
print(config.MONGO_URI)
print(config.REDIS_HOST)
print(config.DATA_DIR)
```

## Using a .env File

It's recommended to create a `.env` file in the project root (excluded by `.gitignore`):

```bash
# .env
JIMMYSPIDER_MONGO_URI=mongodb://localhost:27017/
JIMMYSPIDER_MONGO_DB=jimmyspider
JIMMYSPIDER_REDIS_HOST=127.0.0.1
JIMMYSPIDER_DATA_DIR=./spider_files
```

Then load it with `python-dotenv`:

```python
# At the very top of your spider entry point
from dotenv import load_dotenv
load_dotenv()

# Then import jimmyspider normally
from jimmyspider import JimmySpider
```

## Docker Environment

```dockerfile
FROM python:3.11

ENV JIMMYSPIDER_MONGO_URI=mongodb://mongo:27017/
ENV JIMMYSPIDER_REDIS_HOST=redis
ENV JIMMYSPIDER_DATA_DIR=/data/spider_files

RUN pip install -e .
```

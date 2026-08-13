# Clash 代理池 — Docker 部署 + 节点自动切换

用 Docker 运行 Clash (mihomo)，通过 API 实现多节点健康检测与自动切换。

## 架构

```
docker compose up -d
        │
        ▼
┌─────────────────────────┐
│  Clash 容器 (mihomo)     │
│  ├─ API  : 9099 (仅本机) │ ← ClashManager 通过 API 切换节点
│  └─ 代理 : 7777 (仅本机) │ ← 爬虫请求走这里
└─────────────────────────┘
        │
        ▼
爬虫 (ClashManager)
  ├─ 后台健康检测（每 30s 测所有节点延迟）
  ├─ 节点异常自动切换
  ├─ 每节点下载 100 次后轮换
  └─ 连续 3 次 403 自动切换
```

## 快速开始

```bash
# 1. 准备配置
cp config/config.yaml.example config/config.yaml   # 填入真实订阅节点
cp .env.example .env                                # 修改 CLASH_SECRET

# 2. 启动 Clash 容器
docker compose up -d

# 3. 验证
python scripts/clash-cli.py test      # API + 代理出口连通性
python scripts/clash-cli.py list      # 列出节点健康状态
python scripts/clash-cli.py health    # 测所有节点延迟
python scripts/clash-cli.py switch    # 手动切换节点

# 4. 运行爬虫示例
python spider.py
```

## 文件说明

| 文件 | 说明 |
|------|------|
| `docker-compose.yml` | mihomo 容器，端口 9099/7777 仅绑本机 |
| `config/config.yaml.example` | 脱敏配置模板（含 VLESS/Hysteria2 示例） |
| `.env.example` | Clash API 密钥等环境变量 |
| `scripts/clash-cli.py` | 命令行工具（list/switch/health/test/monitor） |
| `spider.py` | 爬虫 + ClashManager 集成示例 |

## ClashManager 配置项

```python
from jimmyspider.proxy_clash import ClashManager

clash = ClashManager({
    "api_url": "http://127.0.0.1:9099",     # Clash API
    "secret": "your-secret-here",            # API 密钥
    "group_name": "🚀节点选择",              # 策略组
    "proxy_port": 7777,                      # 代理端口
    "max_downloads_per_node": 100,           # 每节点下载上限
    "max_403_errors": 3,                     # 403 触发切换阈值
    "switch_strategy": "round_robin",        # round_robin | random
    "require_post_switch_connectivity": True # 切换后验证出口
})
```

## 注意事项

⚠️ `config/config.yaml` 包含真实订阅凭证，已被 `.gitignore` 排除，切勿提交。

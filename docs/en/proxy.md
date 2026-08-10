# Proxy Configuration Guide

jimmySpider supports two proxy modes.

## Mode 1: Tunnel Proxy

The simplest approach: use a fixed tunnel proxy URL.

### Configuration

```bash
export JIMMYSPIDER_PROXY_TUNNEL_URL="http://user:pass@proxy-host:port"
```

### Usage

```python
from jimmyspider import JimmySpider

class Spider(JimmySpider):
    def run(self):
        # Passing test_url automatically enables the proxy
        res = self.single_fetcher.fetch("https://target-site.com")
```

Once `test_url` is provided, `ProxyUtil` automatically:
1. Sends requests through the tunnel proxy
2. Switches proxy when the speed drops below 30KB/s
3. Automatically retries when an anti-bot page is returned

## Mode 2: Clash Proxy Pool

A multi-node proxy pool with health checks and automatic switching.

### Prerequisites

1. Run the Clash client (with the External Controller enabled)
2. Have at least one proxy policy group in the Clash config

### Configuration

```bash
# Clash API address (default http://127.0.0.1:9097)
export JIMMYSPIDER_CLASH_API_URL="http://127.0.0.1:9097"

# Clash API secret
export JIMMYSPIDER_CLASH_SECRET="your-secret"

# Clash proxy address (default http://127.0.0.1:7897)
export JIMMYSPIDER_CLASH_PROXY_URL="http://127.0.0.1:7897"

# Policy group name (default "自动选择")
export JIMMYSPIDER_CLASH_POLICY_GROUP="🚀 节点选择"
```

### Usage

```python
from jimmyspider.proxy_clash import ClashManager

# Initialize
clash = ClashManager({
    "policy_group": "🚀 节点选择",
})

# Start automatic health checks (every 30 seconds)
clash.start_auto_health_check(interval_sec=30)

# Get the current proxy config
proxy_config = clash.get_proxy_config()

# Manually switch to a healthy node
clash.switch_to_healthy_node()
```

### Health Check Mechanism

`ClashManager` will:
1. Periodically test the latency of all nodes
2. Automatically exclude nodes that time out or fail
3. Automatically switch to a healthy node when a node is unhealthy
4. Sort nodes by region (preferring low-latency regions)

## How Proxies Work Inside Request Handlers

```python
# All request handlers receive test_url at initialization
fetcher = SingleRequestHandler(test_url="https://example.com")

# A proxy is fetched automatically before each request
res = fetcher.fetch("https://target-site.com")

# Internal flow:
# 1. ProxyUtil.get_proxy() fetches a proxy
# 2. Sends the request
# 3. Switches proxy and retries on failure/anti-bot
# 4. Slow speeds also trigger a proxy switch
```

## Custom Proxy Logic

If you don't need the framework's proxy management, you can override it:

```python
from jimmyspider.request import SingleRequestHandler

class CustomFetcher(SingleRequestHandler):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proxy_util = None  # disable proxy

fetcher = CustomFetcher()
res = fetcher.fetch("https://example.com")  # direct connection
```

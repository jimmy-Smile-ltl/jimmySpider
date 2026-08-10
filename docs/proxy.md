# 代理配置指南

jimmySpider 支持两种代理模式。

## 模式一：隧道代理

最简单的代理方式，使用固定的代理隧道 URL。

### 配置

```bash
export JIMMYSPIDER_PROXY_TUNNEL_URL="http://user:pass@proxy-host:port"
```

### 使用

```python
from jimmyspider import JimmySpider

class Spider(JimmySpider):
    def run(self):
        # 传入 test_url 会自动启用代理
        res = self.single_fetcher.fetch("https://target-site.com")
```

传入 `test_url` 参数后，`ProxyUtil` 会自动：
1. 使用隧道代理发起请求
2. 如果请求速度低于 30KB/s，自动切换代理
3. 如果返回反爬页面，自动重试

## 模式二：Clash 代理池

多节点代理池，支持健康检测和自动切换。

### 前置条件

1. 运行 Clash 客户端（开启 External Controller）
2. Clash 配置中至少有一个代理策略组

### 配置

```bash
# Clash API 地址（默认 http://127.0.0.1:9097）
export JIMMYSPIDER_CLASH_API_URL="http://127.0.0.1:9097"

# Clash API 密钥
export JIMMYSPIDER_CLASH_SECRET="your-secret"

# Clash 代理地址（默认 http://127.0.0.1:7897）
export JIMMYSPIDER_CLASH_PROXY_URL="http://127.0.0.1:7897"

# 策略组名称（默认 "自动选择"）
export JIMMYSPIDER_CLASH_POLICY_GROUP="🚀 节点选择"
```

### 使用

```python
from jimmyspider.proxy_clash import ClashManager

# 初始化
clash = ClashManager({
    "policy_group": "🚀 节点选择",
})

# 启动自动健康检测（每 30 秒）
clash.start_auto_health_check(interval_sec=30)

# 获取当前代理配置
proxy_config = clash.get_proxy_config()

# 手动切换到健康节点
clash.switch_to_healthy_node()
```

### 健康检测机制

`ClashManager` 会：
1. 定期测试所有节点的延迟
2. 自动排除超时/失败的节点
3. 在节点异常时自动切换到健康节点
4. 按地区排序节点（优先低延迟地区）

## 代理在请求处理器中的工作方式

```python
# 所有请求处理器在初始化时接收 test_url
fetcher = SingleRequestHandler(test_url="https://example.com")

# 每次请求自动获取代理
res = fetcher.fetch("https://target-site.com")

# 内部流程：
# 1. ProxyUtil.get_proxy() 获取代理
# 2. 发送请求
# 3. 如果失败/反爬，切换代理重试
# 4. 速度过慢也触发代理切换
```

## 自定义代理逻辑

如果不需要框架的代理管理，可以覆盖：

```python
from jimmyspider.request import SingleRequestHandler

class CustomFetcher(SingleRequestHandler):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.proxy_util = None  # 禁用代理

fetcher = CustomFetcher()
res = fetcher.fetch("https://example.com")  # 直连
```

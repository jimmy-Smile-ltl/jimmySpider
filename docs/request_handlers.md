# 请求处理器选择指南

jimmySpider 提供 6 种请求处理器，覆盖从简单爬取到高级反爬场景。

## 对比总览

| 处理器 | 引擎 | 并发模型 | TLS 指纹伪装 | 适用场景 |
|--------|------|---------|-------------|---------|
| `SingleRequestHandler` | requests | 同步单线程 | ❌ | 简单网站、调试 |
| `AsyncRequestHandler` | aiohttp | asyncio 协程 | ❌ | 高并发、IO 密集 |
| `ThreadRequestHandler` | requests | ThreadPoolExecutor | ❌ | 混合 IO、中等并发 |
| `CurlRequestHandler` | curl_cffi | 同步单线程 | ✅ | Cloudflare、TLS 检测 |
| `CurlCffiThreadRequestHandler` | curl_cffi | ThreadPoolExecutor | ✅ | 需要伪装 + 中等并发 |
| `CurlCffiAsyncRequestHandler` | curl_cffi | asyncio | ✅ | 需要伪装 + 高并发 |

## 详细说明

### SingleRequestHandler

最基础的请求处理器，同步阻塞，逐条请求。

```python
fetcher = SingleRequestHandler(test_url="https://example.com")
res = fetcher.fetch("https://example.com/api/data")
```

**适用**: 简单网站、数据量小、调试阶段

### AsyncRequestHandler

基于 aiohttp 的异步协程请求，高并发。

```python
fetcher = AsyncRequestHandler(test_url="https://example.com", max_workers=20)
results = fetcher.fetch_all(url_list)  # 并发请求所有 URL
```

**适用**: 大量 URL 请求、API 数据采集

### ThreadRequestHandler

基于线程池的多线程请求。

```python
fetcher = ThreadRequestHandler(test_url="https://example.com", max_workers=10)
results = fetcher.fetch_all(url_list)
```

**适用**: 中等并发、需要线程上下文

### CurlRequestHandler

curl_cffi 引擎，模拟浏览器 TLS 指纹（默认 chrome120），对抗 Cloudflare 等 TLS 检测。

```python
fetcher = CurlRequestHandler(
    test_url="https://example.com",
    impersonate="chrome110"
)
res = fetcher.fetch("https://example.com/cloudflare-protected")
```

**适用**: Cloudflare 保护、TLS 指纹检测、403 反爬

### CurlCffiThreadRequestHandler

curl_cffi + 多线程，兼具指纹伪装和并发能力。

```python
fetcher = CurlCffiThreadRequestHandler(
    test_url="https://example.com",
    max_workers=10,
    impersonate="chrome120"
)
```

**适用**: 大量 CF 保护页面的并发下载

### CurlCffiAsyncRequestHandler

curl_cffi + asyncio，指纹伪装 + 最高并发性能。

```python
fetcher = CurlCffiAsyncRequestHandler(
    test_url="https://example.com",
    max_workers=20
)
```

**适用**: 高并发 + 反爬场景

## 代理配置

所有处理器在提供 `test_url` 参数时会自动启用代理轮换。

```python
# 自动使用快代理隧道
fetcher = SingleRequestHandler(test_url="https://example.com")

# 或使用 Clash 代理池
fetcher = SingleRequestHandler(test_url="https://example.com", use_clash_pool=True)
```

## 常见反爬场景速查

| 现象 | 根因 | 方案 |
|------|------|------|
| 403 | Cloudflare | 切换到 `CurlRequestHandler` + cf_clearance Cookie |
| 521 | 加速乐 CDN | 双层 JS 挑战，需 Cookie hook |
| 412 | 瑞数 WAF | JSVMP 保护，需 CDP 级诊断 |
| 空响应 | TLS 指纹 | 切换到 curl_cffi (`impersonate="chrome110"`) |
| 验证码 | 滑块/点选 | Playwright + stealth.js 自动化 |
| 截断 | API 限制 | 按天/年/类别分片搜索 |

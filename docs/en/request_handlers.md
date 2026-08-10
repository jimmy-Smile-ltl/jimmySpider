# Request Handler Selection Guide

jimmySpider provides 6 request handlers covering everything from simple scraping to advanced anti-bot scenarios.

## Comparison Overview

| Handler | Engine | Concurrency model | TLS fingerprint impersonation | Best for |
|---------|--------|-------------------|-------------------------------|----------|
| `SingleRequestHandler` | requests | Synchronous single-thread | ❌ | Simple sites, debugging |
| `AsyncRequestHandler` | aiohttp | asyncio coroutines | ❌ | High concurrency, I/O-bound tasks |
| `ThreadRequestHandler` | requests | ThreadPoolExecutor | ❌ | Mixed I/O, medium concurrency |
| `CurlRequestHandler` | curl_cffi | Synchronous single-thread | ✅ | Cloudflare, TLS detection |
| `CurlCffiThreadRequestHandler` | curl_cffi | ThreadPoolExecutor | ✅ | Impersonation + medium concurrency |
| `CurlCffiAsyncRequestHandler` | curl_cffi | asyncio | ✅ | Impersonation + high concurrency |

## Details

### SingleRequestHandler

The most basic handler: synchronous and blocking, fetching one request at a time.

```python
fetcher = SingleRequestHandler(test_url="https://example.com")
res = fetcher.fetch("https://example.com/api/data")
```

**Best for**: simple sites, small data volumes, debugging

### AsyncRequestHandler

Async coroutine requests based on aiohttp; high concurrency.

```python
fetcher = AsyncRequestHandler(test_url="https://example.com", max_workers=20)
results = fetcher.fetch_all(url_list)  # concurrently requests all URLs
```

**Best for**: many URL requests, API data collection

### ThreadRequestHandler

Thread-pool-based multithreaded requests.

```python
fetcher = ThreadRequestHandler(test_url="https://example.com", max_workers=10)
results = fetcher.fetch_all(url_list)
```

**Best for**: medium concurrency, scenarios needing thread context

### CurlRequestHandler

curl_cffi engine that mimics browser TLS fingerprints (default chrome120) to defeat TLS detection such as Cloudflare.

```python
fetcher = CurlRequestHandler(
    test_url="https://example.com",
    impersonate="chrome110"
)
res = fetcher.fetch("https://example.com/cloudflare-protected")
```

**Best for**: Cloudflare-protected sites, TLS fingerprint detection, 403 anti-bot

### CurlCffiThreadRequestHandler

curl_cffi + multithreading: both fingerprint impersonation and concurrency.

```python
fetcher = CurlCffiThreadRequestHandler(
    test_url="https://example.com",
    max_workers=10,
    impersonate="chrome120"
)
```

**Best for**: concurrent downloads of many Cloudflare-protected pages

### CurlCffiAsyncRequestHandler

curl_cffi + asyncio: fingerprint impersonation with top concurrency performance.

```python
fetcher = CurlCffiAsyncRequestHandler(
    test_url="https://example.com",
    max_workers=20
)
```

**Best for**: high concurrency + anti-bot scenarios

## Proxy Configuration

All handlers automatically enable proxy rotation when a `test_url` parameter is provided.

```python
# Automatically uses the Kuaidaili tunnel proxy
fetcher = SingleRequestHandler(test_url="https://example.com")

# Or use the Clash proxy pool
fetcher = SingleRequestHandler(test_url="https://example.com", use_clash_pool=True)
```

## Common Anti-Bot Scenario Cheat Sheet

| Symptom | Root cause | Solution |
|---------|------------|----------|
| 403 | Cloudflare | Switch to `CurlRequestHandler` + cf_clearance Cookie |
| 521 | Jiasule CDN | Double-layer JS challenge; needs a cookie hook |
| 412 | Ruishu WAF | JSVMP protection; needs CDP-level diagnosis |
| Empty response | TLS fingerprint | Switch to curl_cffi (`impersonate="chrome110"`) |
| CAPTCHA | Slider/click CAPTCHA | Playwright + stealth.js automation |
| Truncation | API limits | Shard searches by day/year/category |

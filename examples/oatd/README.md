# OATD 学位论文抓取 (oatd)

## 站点

- 站点页面：https://oatd.org — Open Access Theses and Dissertations（开放获取学位论文库），聚合全球高校学位论文元数据
- 搜索接口：Solr 风格，`q=*:* AND pub_dt:[年份区间]` 按出版年份过滤，`start` 参数翻页（每页 30 条）
- 站点部署在 **Cloudflare** 之后，直接请求会返回 403 "Just a moment..." Turnstile 挑战页；访问过快返回 "Server Too Busy - Please slow down"

## 展示特性

- **列表 + 详情两种模式对照**：
  - `spider.py`：完整版 — 列表页只取链接，详情页用 **AsyncRequestHandler（aiohttp + asyncio.Semaphore）一次 `fetch_all()` 批量并发**抓取一页内全部论文
  - `spider_list.py`：仅列表版 — 15 线程解析单页列表条目（不抓详情页），年份内所有页 URL 预先构建后 10 线程并发翻页
- **Cloudflare Cookie 刷新**：403 挑战触发 `CookieFlush` 无头 Chrome 刷新 cookie；`threading.Lock` + `is_flushing` 标志 + 60 秒冷却保证**同一时刻只有一个线程刷新**，其余线程忙等
- **Turnstile 挑战破解**（cookie_flush_playwright_cdp.py）：DOM 状态机 `loading → ready → click → verifying → solved`，点击 Turnstile 复选框后等待验证完成，超时/失败递归重试
- **真实 Chrome + CDP 接入**：每次启动全新 Chrome 进程（独立临时 `user-data-dir` + 空闲 CDP 端口），无自动化启动标志，指纹自然；可选注入 `stealth.min.js`（`Page.addScriptToEvaluateOnNewDocument`）
- **拟人鼠标轨迹**：三次贝塞尔曲线 + 微抖动 + 变速（两端慢中间快），先移到附近再精确点击、按下/释放间停顿
- **cookie 可用性验证**：`test_cookie()` 用刷新出的 cookie 实请求一次，200 才把 cookie 交给爬虫
- **代理轮换**：`SingleRequestHandler` / `AsyncRequestHandler` 传入真实 `test_url` 后，内置 ProxyUtil 自动轮换代理并逐个验证有效性
- **Redis 多层断点**：已完成年份列表、当年 `start` 游标、错误页 URL 集合，任意中断可恢复
- **按年全量爬取**：Solr 按年查询（spider.py 从 1800 正序到当前年，spider_list.py 从当前年倒序到 2000），每页解析 `matchesReport` 总匹配数推算进度
- **`_id` 生成策略**：详情页优先 DOI（正则 `10.\d{4,9}/...` → `generate_doi_id`），无 DOI 用 record id 或标题哈希
- **详情页字段兜底**：`recordTable` 表格解析异常或无 Title 时，自动回退从列表页条目提取（`extract_info_by_list`）

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 列表 + 详情（异步版）：按年翻页，AsyncRequestHandler 批量并发详情页，MongoDB + HTML 落盘 |
| `spider_list.py` | 仅列表版：多线程解析列表条目入库，不抓详情页 |
| `cookie_flush_playwright_cdp.py` | Cloudflare Cookie 刷新器（CookieFlush 类）：CDP 启动真实 Chrome，Turnstile 状态机破解 + 拟人鼠标 + cookie 验证 |

## 运行方式

```bash
cd examples/oatd
python cookie_flush_playwright_cdp.py   # 单独测试 cookie 刷新（打开浏览器手动观察）
python spider.py                        # 完整版：列表 + 异步详情抓取
python spider_list.py                   # 仅列表版
```

## 前置条件

- **Chrome/Chromium 已安装**：cookie_flush 需要可执行文件（`_find_chrome` 在 `google-chrome` / `chromium` 等常见路径中查找），Linux 环境
- **Playwright**：`pip install playwright`（本示例用 `connect_over_cdp` 直连外部 Chrome 进程，无需 `playwright install` 内置浏览器）
- `stealth.min.js`（可选）：放本目录可自动通过 CDP 注入，不随示例发布，不注入也能运行
- 依赖服务：
  - **Redis**：已完成年份（`oatd_log_finished_year`）、start 游标（`oatd_log_current_start`）、错误页 URL 集合（spider_list 版 `oatd_log_error_page_url`）
  - **MongoDB**：结果存储，collection 名 `oatd`
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置
- 代理（可选）：配置 `test_url` 后自动启用 ProxyUtil 代理轮换

## 爬虫架构

**spider.py（列表 + 异步详情）**：

```
run()
 ├─ 读取已完成年份列表
 ├─ for year in 1800..当前年
 │   ├─ 未完成才抓，先 flush_cookies()（60s 冷却 + is_flushing 互斥）
 │   └─ handle_one_year(year)
 │       └─ while 有下一页（Solr start 翻页）
 │           ├─ search_one_page(page_url)
 │           │   ├─ 200 + "Search Limiters" → 成功
 │           │   ├─ "Server Too Busy" → sleep 5*retry
 │           │   ├─ 403 "Just a moment..." → 加锁 flush_cookies 后重试
 │           │   └─ 最多 20 次重试
 │           ├─ 解析 #results > div.result 论文列表
 │           ├─ async_handler.fetch_all(全部详情URL)   # aiohttp + Semaphore 一次并发
 │           │   └─ 每篇: recordTable 解析 → HTML 落盘 → MongoDB（_id=DOI/标题哈希）
 │           └─ 更新 start 游标（log_current_start）
 │   └─ 完成后年份记入 log_finished_year
```

**spider_list.py（仅列表）**：

```
run()
 ├─ for year in 当前年..2000（倒序）
 │   ├─ 第 1 页先抓：得 max_start（matchesReport 总匹配数）
 │   ├─ 预构建所有 start 步进（30/页）的页 URL
 │   ├─ ThreadPoolExecutor(10) 并发抓各页
 │   │   └─ 每页 ThreadPoolExecutor(15) 并发 extract_info_by_list 解析条目
 │   │       └─ MongoDB（_id = record id / DOI / 标题哈希）
 │   └─ 异常页 URL 记入 log_error_page_url
```

**cookie_flush_playwright_cdp.py（Turnstile 破解）**：

```
CookieFlush.flush(url)
 ├─ 启动全新 Chrome（--remote-debugging-port + 临时 user-data-dir + 无自动化标志）
 ├─ playwright.chromium.connect_over_cdp() 接入，新建 context + page
 ├─ goto(url) → _ts_state() 查询 Turnstile DOM 状态
 │   └─ 状态机: none / loading / ready / verifying / solved / slow_down
 ├─ _solve_cloudflare(): loading→等待 ready→定位复选框坐标→拟人点击→等待 solved，失败递归重试
 ├─ 提取 context cookies（等待 cf_clearance 出现）
 └─ test_cookie() 实请求验证，通过才返回 cookie 字典
```

数据流向：Solr 搜索页 HTML → 论文链接/条目 → （详情页 HTML → recordTable 字段 → 原始 HTML 落盘）→ MongoDB；Cloudflare 挑战 → CookieFlush 实时刷新 `cf_clearance` 等 cookie 供后续请求使用。

## 核心代码片段

**Cookie 刷新互斥**（Lock + 标志位 + 冷却，多线程只刷一次）：

```python
def flush_cookies(self, url):
    now_time = time.time()
    if abs(now_time - self.flush_time) < 60 or self.is_flushing:
        while True:                       # 别的线程正在刷新，忙等
            if not self.is_flushing:
                break
            time.sleep(2)
    else:
        self.is_flushing = True
        cookies = self.cookie_flusher.flush(url)
        self.cookies.update(cookies)
        self.flush_time = time.time()
        self.is_flushing = False
```

**反爬响应分诊**（403 挑战 / 限速 / 参数错误三种情况分别处理）：

```python
if response.status_code == 200 and "Search Limiters" in response.text:
    return response
elif "Server Too Busy - Please slow down" in response.text:
    time.sleep(5 * retry)                 # 限速：累积等待
elif response.status_code == 403 and "Just a moment..." in response.text:
    with self.global_lock:
        self.flush_cookies(self.search_url)
        time.sleep(2)
        continue                          # CF 挑战：刷 cookie 重试
else:
    self.log_error_page_url.add_to_set(page_url)   # 参数问题：记录后放弃
    return None
```

**一页内全部详情页异步并发**（AsyncRequestHandler 替代线程池的写法）：

```python
async_results = self.async_handler.fetch_all(
    [item["paper_url"] for item in paper_item_list],
    headers=self.headers,
    cookies=self.cookies,
)
for paper_item in paper_item_list:
    html_text = async_results.get(paper_item["paper_url"])
    if html_text:
        info_dict = self.handle_one_paper(paper_item["paper_url"],
                                          paper_item["one_paper_tag"], html_text)
```

**Turnstile DOM 状态机**（loading → ready → verifying → solved）：

```python
def _ts_state(self):
    """查询 Turnstile widget 的 DOM 状态。
    "none"     — 无挑战，已通过或无需验证
    "loading"  — 标题含 "Just a moment"，widget 未渲染
    "ready"    — checkbox/iframe 已渲染，可以点击
    "verifying"— 已点击，验证中（spinner 可见）
    "solved"   — widget 已从 DOM 消失，验证通过
    "slow_down"— 访问频率过高
    """
    return self.page.evaluate("""() => { ... }""")
```

**拟人鼠标移动**（贝塞尔曲线 + 微抖动 + 变速）：

```python
# 三次贝塞尔曲线插值 + 中间段 ±1.5px 手抖 + 两端慢中间快
delay = 0.003 + 0.012 * (1 - abs(2 * progress - 1))
self.page.mouse.move(px, py)
time.sleep(delay)
```

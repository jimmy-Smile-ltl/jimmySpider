# 中金公司研报列表抓取 (cicc_report)

## 站点

- 站点页面：https://www.cicc.com/business/list_214_223_1.html — 中金公司（CICC）研究报告列表，按页分页，条目直接含研报 PDF 链接
- 列表 URL 模板：`https://www.cicc.com/business/list_214_223_{page}.html`（纯 HTML 页面，无 JSON 接口）
- 反爬：站点由**加速乐（JSL）CDN** 防护，首次请求返回 JS 挑战页（`document.cookie` 赋值表达式 或 `go({...})` 加密数据），需计算出 `__jsl_clearance_s` cookie 才能拿到真实页面

## 展示特性

- **加速乐 (JSL) cookie 挑战自动化**，覆盖两种挑战变体：
  - 简单变体：正则提取 `document.cookie = ...` 赋值表达式，用 `execjs` 直接求值得到 `__jsl_clearance_s`
  - 加密变体：页面返回 `go({...})` 数据（含加密参数），需调用还原出的 JS 函数 `get_cookie(go_data)` 计算 clearance cookie
  - 携带 cookie 重试，直到拿到不含挑战标记的真实页面；成功后 cookie 缓存在内存中，后续分页直接复用
- **放松响应校验**：请求时显式传 `check_size=False` / `check_status_code=False`，避免挑战页（内容小、状态码异常）被请求处理器按“反爬/异常”提前丢弃
- **分页 URL 模板 + 总页数解析**：页码直接拼 URL；总页数从 `div.jump > input` 的 `data-page-max` 属性（或 `div.jump > span` 中 `1/N` 文本）解析
- **Redis 断点续爬**：进度以 JSON 序列化（`{"page": N}`）写入 `log_page`，精确恢复到下一页；失败页入 Set，主流程后最多 3 轮重试
- **PDF 链接识别**：条目 `file_url` 由 `urljoin` 拼接详情 href，`.pdf` 结尾自动标记 `file_type="pdf"`，为后续 `file_saver` 批量下载做准备
- 页间 `time.sleep(1)` 限速，降低被挑战的触发频率

## 文件说明

| 文件 | 说明 |
|------|------|
| `spider.py` | 主爬虫（单文件）：JSL 解挑战 → 列表解析 → 入库 → 断点与重试 |
| `main_兼容不同hash.js` | 加速乐加密变体的 cookie 计算脚本（**JS 逆向研究产物，未随示例发布**，需自行放置，见前置条件） |

## 运行方式

```bash
cd examples/cicc_report
python spider.py
```

## 前置条件

- **`pyexecjs`**：解析 JS 挑战所需，`pip install pyexecjs`
- **`main_兼容不同hash.js`（必需，未随包发布）**：加速乐加密变体 `go({...})` 的还原脚本。该文件是 JS 逆向研究的产物，属于站方 JS 的反混淆/还原实现，出于合规考虑不随示例分发。使用时：
  - 将还原脚本放到本目录并保持文件名一致（`_load_cookie_js_ctx` 从 `Path(__file__).parent` 下加载）
  - 脚本需在 JS 侧导出 `get_cookie(go_data)` 函数，返回 `__jsl_clearance_s` 字符串
  - 或改写 `_load_cookie_js_ctx` / `_compute_clearance` 指向自己的实现
- 无需登录 cookie；`test_url` 用于请求处理器装配代理（未配置代理则直连）
- 依赖服务：
  - **Redis**：断点续爬（key 前缀 `cicc_report_`）
  - **MongoDB**：结果存储，collection 名 = 目录名 `cicc_report`，按 `_id` upsert
- `jimmyspider` 框架已安装；Redis / MongoDB 连接参数通过环境变量或 `jimmyspider.yaml` 配置

> 说明：本示例默认使用 `SingleRequestHandler`（requests）。若站点升级为按 TLS 指纹拦截，可将 `self.single_fetcher` 换为框架内置的 `CurlRequestHandler`（curl_cffi + `impersonate="chrome120"` 模拟浏览器指纹），调用方式完全一致。

## 爬虫架构

```
run_all()
 ├─ 读取序列化断点 {"page": N}
 ├─ while True
 │   ├─ get_list_page(page)
 │   │   ├─ 已有 _cookies：直接带 cookie 请求，无挑战标记即返回
 │   │   └─ 否则 _solve_challenge(url)
 │   │       ├─ 首次请求 → 提取 document.cookie 表达式 → execjs 求值
 │   │       ├─ 仍含 go({...}) → 还原 JS get_cookie 计算 clearance
 │   │       ├─ 带 cookie 重试至真实页面，缓存 self._cookies
 │   ├─ parse_list_page(html)
 │   │   ├─ div.jump → data-page-max / "1/N" → 总页数
 │   │   └─ div.ui-article-list > div.item → 标题/时间/PDF 链接
 │   ├─ save_result()                    # MongoDB upsert
 │   ├─ log_page.record_string({"page": page+1})
 │   └─ time.sleep(1)
 ├─ 主流程结束，log_page 清空
 └─ 最多 3 轮 handle_error_page() 重试失败页
```

数据流向：挑战页 → 计算 `__jsl_clearance_s` → 真实列表 HTML → BeautifulSoup 提取 标题/发布时间/文件链接 → MongoDB（`_id` 为文件链接或标题的 MD5）。

## 核心代码片段

**JSL 挑战主流程**（简单变体求值 + 加密变体还原计算，两步按需执行）：

```python
def _solve_challenge(self, url):
    response = self.single_fetcher.fetch(url, headers=self.headers, cookies={},
                                         method="GET", check_size=False,
                                         check_status_code=False, stream=False)
    cookies = response.cookies.get_dict()
    html_text = response.text

    cookie_expr = self._extract_cookie_expr(html_text)          # document.cookie = ... 表达式
    if cookie_expr:
        cookie_value = self._eval_cookie_expr(cookie_expr)      # execjs 求值
        if cookie_value:
            cookie_dict = self._cookie_str_to_dict(cookie_value)
            if cookie_dict.get("__jsl_clearance_s"):
                cookies["__jsl_clearance_s"] = cookie_dict["__jsl_clearance_s"]
        # 带 cookie 重试，拿到下一层页面
        response = self.single_fetcher.fetch(url, headers=self.headers, cookies=cookies, ...)
        html_text = response.text

    go_data = self._extract_go_data(html_text)                  # go({...}) 加密数据
    if go_data:
        clearance = self._compute_clearance(go_data)            # 还原 JS: get_cookie(go_data)
        if clearance:
            cookies["__jsl_clearance_s"] = clearance
            response = self.single_fetcher.fetch(url, headers=self.headers, cookies=cookies, ...)
            if not response or response.status_code != 200:
                return None
            html_text = response.text

    self._cookies = cookies
    return html_text
```

**挑战识别与表达式提取**：

```python
@staticmethod
def _is_challenge(html_text: str) -> bool:
    return "document.cookie" in html_text or "go(" in html_text

@staticmethod
def _extract_cookie_expr(html_text: str) -> Optional[str]:
    match = re.search(r"document\.cookie\s*=\s*(.+?)\s*location", html_text, re.DOTALL)
    return match.group(1) if match else None
```

**总页数解析**（两个候选源依次尝试）：

```python
input_tag = soup.select_one("div.jump > input")
if input_tag and str(input_tag.attrs.get("data-page-max", "")).isdigit():
    return int(input_tag.attrs["data-page-max"])
span = soup.select_one("div.jump > span")
match = re.search(r"(\d+)/(\d+)", span.get_text(" ", strip=True))
return int(match.group(2)) if match else 0
```

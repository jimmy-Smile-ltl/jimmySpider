import asyncio
import hashlib
import logging
import os
import random
import sys
import threading
import time
from typing import List, Dict, Optional
import functools
import aiohttp
import certifi
import curl_cffi
from curl_cffi import requests as curl_cffi_requests
from concurrent.futures import ThreadPoolExecutor, as_completed
# curl_requests.get(article_url, headers=headers,impersonate="chrome110")
from curl_cffi import requests as curl_requests
import requests
from jimmyspider.proxy import ProxyUtil

# ---------------------------------------------------------------------------
# --- 最终版：提供同步接口的异步协程请求处理器 ---
# ---------------------------------------------------------------------------
class AsyncRequestHandler:
    """
    使用 asyncio 和 aiohttp 实现的高性能异步Web请求类。
    提供一个简单的同步接口 fetch_all()，内部使用异步并发。
    """

    def __init__(self, method: str = 'GET', max_workers: int = 10, test_url: Optional[str] = None):
        """
        初始化异步请求处理器。
        只存储配置，不创建任何与事件循环相关的对象。

        :key test_url (str, optional): 用于初始化代理。
        :key max_workers (int, optional): 最大并发协程数。默认为 100。
        :key headers (dict, optional): 全局请求头。
        :key cookies (dict, optional): 全局Cookies。
        :key method (str, optional): 默认请求方法 (GET/POST)。默认为 'GET'。
        """
        self.test_url = test_url
        self.max_workers = max_workers
        self.proxy_util = ProxyUtil(test_url) if test_url else None
        self.method = method

    async def _fetch_one(self, session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore,
                         retry_count: int = 10, **kwargs) -> Dict[str, Optional[str]]:
        """
        [内部方法] 异步获取单个URL的内容，并包含重试逻辑。

        """
        for attempt in range(retry_count):
            sleep_time = 1 + attempt % 5
            try:
                async with semaphore:
                    proxy = self.proxy_util.get_proxy() if self.proxy_util else None
                    proxy_url = proxy.get('https') if proxy else None
                    try:
                        request_kwargs = {
                            'proxy': proxy_url,
                            'timeout': 10,
                            **kwargs
                        }
                        if self.method.upper() == 'POST':
                            request_coro = session.post(url, **request_kwargs)
                        else:
                            request_coro = session.get(url, **request_kwargs)
                        async with request_coro as response:
                            response.raise_for_status()
                            content = await response.text(encoding='utf-8', errors='ignore')
                            return {url: content,"success":True}
                    except TimeoutError as e:
                        await asyncio.sleep(sleep_time)
                    except  aiohttp.client_exceptions.ClientHttpProxyError as e:
                        await asyncio.sleep(sleep_time)
                    except Exception as e:
                        # print("还没有捕获的错误 "+ str(e))
                        await asyncio.sleep(sleep_time)
            except Exception as e:
                # print("按道理，不应该的报错，内部，应该捕获了的，"+str(e),end=" ")
                await asyncio.sleep(1)
        else:
            # 如果所有重试都失败，返回None
            return  {url: None,"success":False}


    async def _fetch_all_async(self, url_list: List[str], **kwargs) -> Dict[str, Optional[str]]:
        """
        [内部方法] 并发获取所有URL的内容，并显示进度。
        这是真正的异步核心。
        """
        # --- 关键设计：在这里创建Semaphore和ClientSession ---
        # 确保它们在当前正在运行的事件循环中被创建。
        semaphore = asyncio.Semaphore(self.max_workers)
        results = {}
        total_urls = len(url_list)

        async with aiohttp.ClientSession() as session:
            future_list = [
                asyncio.create_task(self._fetch_one(session, url, semaphore, **kwargs))
                for url in url_list
            ]

            completed_count = 0
            success_count = 0
            for future in asyncio.as_completed(future_list):
                try:
                    result = await future
                    is_success = result['success']
                    if is_success:
                        success_count += 1
                    results.update(result)
                except Exception as e:
                    print(f"处理URL时发生错误: {e}")
                completed_count += 1
                percentage = (completed_count / total_urls) * 100
                print(f"\r网络请求  进度: {completed_count}/{total_urls}  其中  成功: {success_count} 失败：{completed_count - success_count} ({percentage:.2f}%)", end="")
        print(f"  模式：异步 Async  并发数：{self.max_workers}   所有requests任务已处理完毕。")
        return results

    def fetch_all(self, url_list: List[str] | List[dict], **kwargs) -> Dict[str, Optional[str]]:
        """
        [公开方法] 同步调用接口，并发获取所有URL的内容。
        """
        if not url_list:
            return {}
        try:
            # 使用 asyncio.run() 启动异步核心逻辑，并阻塞直到完成
            results = asyncio.run(self._fetch_all_async(url_list, **kwargs))
            if "success" in results:
                results.pop("success")
            return results
        except RuntimeError as e:
            # 捕获 "cannot run loop while another loop is running" 错误
            if "cannot run loop while another loop is running" in str(e):
                print("\n错误：检测到您正在一个已有的事件循环中调用同步的 fetch_all 方法。")
                print("请在异步环境中使用 await handler._fetch_all_async(...)")
                return {}
            else:
                raise e
# 协程 处理大量请求

# 多线程版本

class ThreadRequestHandler:
    def __init__(self, test_url, max_workers=10, headers=None, cookies=None, method='GET', retry_count=5):
        if test_url:
            self.proxyUtil = ProxyUtil(test_url=test_url)
            self.proxies = self.proxyUtil.get_proxy()
        else:
            self.proxies = None
        self.max_workers = max_workers
        self.headers = headers
        self.cookies = cookies
        self.method = method
        self.retry_count = retry_count

    def fetch(self, url, *args, **kwargs):
        retry_count = self.retry_count
        for attempt in range(retry_count):
            try:
                if self.method.upper() == 'POST':
                    res = requests.post(url, headers=self.headers, cookies=self.cookies, proxies=self.proxies,
                                        timeout=30, *args,**kwargs)
                else:
                    res = requests.get(url, headers=self.headers, cookies=self.cookies, proxies=self.proxies,
                                       timeout=30,*args, **kwargs)
                res.raise_for_status()
                res.encoding = res.apparent_encoding
                return {url: res.text}
            except Exception as e:
                sleep_duration = random.randint(2, 8)
                time.sleep(sleep_duration)
                # print(f"第{attempt + 1}次请求失败: {url} 错误: {e}")
                if self.proxies:
                    self.proxies = self.proxyUtil.get_proxy()
                if attempt == retry_count - 1:
                    print(f"第{attempt + 1}次请求失败: {url} 错误: {e}")
                    # breakpoint()
                    break
        print(f"访问失败 {url}  重试次数:{self.retry_count} <UNK>")
        return {url: None}

    def fetch_all(self, url_list, *args,**kwargs) -> dict:
        results = {}
        total_pdfs = len(url_list)
        # 输出处理的进度
        completed_count = 0
        # 计算每多少个任务报告一次进度，至少为1
        progress_interval = max(1, total_pdfs // 10)
        # 使用字典将 future 映射回 url，这样出错时可以知道是哪个url
        if len(url_list) == 0:
            return results
        # 进度打印
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self.fetch, url ,*args,**kwargs) for url in url_list]
            for future in as_completed(futures):
                results.update(future.result())
                completed_count += 1
                percentage = (completed_count / total_pdfs) * 100
                # 每完成一个区段的任务，或者全部完成时，打印进度
                # if completed_count % progress_interval == 0 and completed_count != total_pdfs:
                    # 使用 `\r` 和 `end=''` 可以在同一行刷新进度，看起来更美观
                print(
                    f"requests by {self.max_workers} thread  进度: {completed_count}/{total_pdfs} ({percentage:.2f}%)",
                    end='\r')
            print(
                f" \r 进度: {completed_count}/{total_pdfs} ({percentage:.2f}%  requests by {self.max_workers} thread 所有任务处理完成。")
        return results

class SingleRequestHandler:
    def __init__(self, test_url=None,use_clash_pool =False):
        self.use_clash_pool = use_clash_pool
        self.test_url = test_url
        if all([test_url, use_clash_pool]):
            print("警告：同时提供 test_url 和 use_clash_pool=True，优先使用 test_url 获取代理")
        if test_url:
            self.proxyUtil = ProxyUtil(test_url=test_url)
            self.proxies = self.proxyUtil.get_proxy()
        elif use_clash_pool:
            self.proxyUtil = ProxyUtil(test_url=test_url)
            self.proxies = self.proxyUtil.get_clash_proxy()
        else:
            self.proxyUtil = None
            self.proxies = None

    def _is_blocked(self, res) -> bool:
        return (
            res.status_code in (403, 404) or
            not res.text or
            "<title>反作弊页面_360问答</title>" in res.text or
            "请进行人机身份验证" in res.text
        )

    def _refresh_proxy(self):
        if self.test_url:
            self.proxies = self.proxyUtil.get_proxy()
        elif  self.use_clash_pool:
            self.proxies = self.proxyUtil.get_clash_proxy()

    def _do_request(self, method: str, url: str, use_proxy: bool,
                    headers, cookies, timeout, stream: bool, **kwargs):
        proxies = self.proxies if use_proxy else None
        fn = requests.post if method == "POST" else requests.get
        return fn(
            url,
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            proxies=proxies,
            stream=stream,
            **kwargs
        )

    def _check_stream_speed(self, res, min_speed_bytes: int,
                            check_interval: float):
        """
        边消费 iter_content 边缓存，同时检测速度。
        返回 (True, content_bytes) 或 (False, b"")
        """
        chunks = []
        window_bytes = 0
        window_start = time.time()

        for chunk in res.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            chunks.append(chunk)
            window_bytes += len(chunk)

            now = time.time()
            elapsed = now - window_start
            if elapsed >= check_interval:
                if window_bytes / elapsed < min_speed_bytes:
                    return False, b""
                window_bytes = 0
                window_start = now

        return True, b"".join(chunks)

    def fetch(self, url, headers=None, cookies=None, method="GET",
              retry_count=5, check_size=True,check_status_code = True,
              stream=False,
              min_speed_bytes: int = 30 * 1024,
              speed_check_interval: float = 10.0,
              **kwargs) -> Optional[object]:
        """
        stream=False：普通请求，自动检测反爬和内容大小。
        stream=True ：流式下载，速度检测通过后将完整内容写入 res.content 返回。
        """
        start_time = time.time()
        method = method.upper()
        proxy_cutoff = int(retry_count / 3 * 2) + 1

        # stream 下载不限总时长，普通请求用常规超时
        if stream:
            timeout = (kwargs.pop("connect_timeout", 15), None)
        else:
            timeout = kwargs.pop("timeout", (15, 60))

        for attempt in range(retry_count):
            use_proxy = bool(self.proxies) and attempt < proxy_cutoff
            try:
                res = self._do_request(
                    method, url,
                    use_proxy=use_proxy,
                    headers=headers,
                    cookies=cookies,
                    timeout=timeout,
                    stream=stream,
                    **kwargs
                )
                if check_status_code:
                    res.raise_for_status()
                else:
                    return res

                if stream:
                    speed_ok, content = self._check_stream_speed(
                        res, min_speed_bytes, speed_check_interval
                    )
                    if not speed_ok:
                        time.sleep(random.randint(1, 3))
                        self._refresh_proxy()
                        continue
                    res._content = content
                    return res

                # 普通请求：内容大小检查
                if check_size and (not res.content or len(res.content) < 100):
                    print(f"内容过小（<100字节），url={url}")
                    continue

                res.encoding = res.apparent_encoding

                # 反爬检测
                if self._is_blocked(res):
                    time.sleep(random.randint(1, 3))
                    self._refresh_proxy()
                    continue

                return res

            except Exception as e:
                time.sleep(random.randint(1, 3))
                self._refresh_proxy()
                if attempt == retry_count - 1:
                    elapsed = time.time() - start_time
                    # print(f"请求失败: 用时{elapsed:.2f}s 重试{attempt + 1}次 error={e} url={url}")
                    return None

        return None



class CurlRequestHandler:
    def __init__(self, test_url=None, use_clash_pool=False, impersonate="chrome120"):
        self.ca_bundle_path = certifi.where()
        self.test_url = test_url
        self.use_clash_pool = use_clash_pool
        self.impersonate = impersonate
        if all([test_url, use_clash_pool]):
            print("警告：同时提供 test_url 和 use_clash_pool=True，优先使用 test_url 获取代理")
        if test_url:
            self.proxyUtil = ProxyUtil(test_url=test_url)
            self.proxies = self.proxyUtil.get_proxy()
        if use_clash_pool:
            self.proxyUtil = ProxyUtil(test_url=test_url)
            self.proxies = self.proxyUtil.get_clash_proxy()
        else:
            self.proxyUtil = None
            self.proxies = None

    def _is_blocked(self, res) -> bool:
        text = res.text
        return (
            text == "" or
            "<title>反作弊页面_360问答</title>" in text or
            "请进行人机身份验证" in text
        )

    def _refresh_proxy(self):
        if self.test_url:
            self.proxies = self.proxyUtil.get_proxy()
        if self.use_clash_pool:
            self.proxies = self.proxyUtil.get_clash_proxy()

    def _do_request(self, method: str, url: str, use_proxy: bool,
                    impersonate: str, stream: bool, **kwargs):
        proxies = self.proxies if use_proxy else None
        fn = curl_requests.post if method == "POST" else curl_requests.get
        kwargs.pop("timeout", None)
        return fn(
            url,
            proxies=proxies,
            verify=self.ca_bundle_path,
            impersonate=impersonate,
            stream=stream,
            timeout=0,
            **kwargs
        )

    def _check_stream_speed(self, res, min_speed_bytes: int, check_interval: float):
        chunks = []
        window_bytes = 0
        window_start = time.time()
        total = 0

        try:
            for chunk in res.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                chunks.append(chunk)
                window_bytes += len(chunk)
                total += len(chunk)

                now = time.time()
                elapsed = now - window_start
                if elapsed >= check_interval:
                    speed = window_bytes / elapsed
                    print(f"速度检测: {speed / 1024:.1f}KB/s 已下载 {total / 1024:.1f}KB")
                    if speed < min_speed_bytes:
                        # 妈的 大多 几十 kb/s
                        print(f"速度不达标，放弃")
                        return False, b""
                    window_bytes = 0
                    window_start = now

            print(f"下载完成，总计 {total / 1024:.1f}KB")
            return True, b"".join(chunks)

        except Exception as e:
            # 真正的错误在这里
            print(f"iter_content 异常: {e}，已下载 {total / 1024:.1f}KB")
            return False, b""

    def fetch(self, url, headers=None, cookies=None, method="GET",
              retry_count=10, impersonate=None,
              stream=False,
              min_speed_bytes: int = 30 * 1024,
              speed_check_interval: float = 5.0,
              **kwargs):
        if impersonate is None:
            impersonate = self.impersonate
        """
        stream=False：普通请求，返回 response 或 None。
        stream=True ：流式请求，边下载边检测速度，速度达标返回带完整 content 的 response，否则重试，最终返回 None。
        """
        method = method.upper()
        for attempt in range(retry_count):
            use_proxy = bool(self.proxies)
            try:
                res = self._do_request(
                    method, url,
                    use_proxy=use_proxy,
                    impersonate=impersonate,
                    stream=stream,
                    headers=headers,
                    cookies=cookies,
                    **kwargs
                )
                res.raise_for_status()
                if stream:
                    speed_ok, content = self._check_stream_speed(
                        res, min_speed_bytes, speed_check_interval
                    )
                    if not speed_ok:
                        time.sleep(1)
                        self._refresh_proxy()
                        continue
                    # 内容挂回 res，调用方直接用 res.content，用法与普通请求一致
                    res._content = content
                    return res

                if self._is_blocked(res):
                    time.sleep(1)
                    self._refresh_proxy()
                    continue

                return res

            except Exception as e:
                time.sleep(1)
                self._refresh_proxy()

        return None

# class CurlRequestHandler:
#     def __init__(self, test_url):
#         self.ca_bundle_path = certifi.where()  # 有中文路径，报错，是基于linux下面的一个包开发的，支持性欠缺
#
#         if test_url:
#             self.proxyUtil = ProxyUtil(test_url=test_url)
#             self.proxies = self.proxyUtil.get_proxy()
#         else:
#             self.proxies = None
#
#     def fetch(self, url, headers=None, cookies=None, method='GET', retry_count=10, **kwargs):
#         start_time = time.time()
#         if "impersonate" in kwargs:
#             impersonate = kwargs.pop("impersonate")
#         else:
#             impersonate = "chrome120"
#
#         for attempt in range(retry_count):
#             try:
#                 if attempt <= retry_count / 3 * 2 + 1:
#                     if method.upper() == 'POST':
#                         res = curl_requests.post(url, headers=headers, cookies=cookies, proxies=self.proxies,
#                                                  verify=self.ca_bundle_path, impersonate = impersonate, **kwargs
#                                                     )
#                     else:
#                         res = curl_requests.get(url, headers=headers, cookies=cookies, proxies=self.proxies,
#                                                 verify=self.ca_bundle_path, impersonate = impersonate, **kwargs
#                                                 )
#                 else:
#                     if method.upper() == 'POST':
#                         res = curl_requests.post(url, headers=headers, cookies=cookies, proxies=None,
#                                                  verify=self.ca_bundle_path, impersonate = impersonate, **kwargs
#                                                  )
#                     else:
#                         res = curl_requests.get(url, headers=headers, cookies=cookies, proxies=None,
#                                                 verify=self.ca_bundle_path, impersonate = impersonate, **kwargs
#                                                 )
#                 res.raise_for_status()
#                 if res.text == "" or res.text.find( "<title>反作弊页面_360问答</title>") != -1 or res.text.find("请进行人机身份验证") != -1:
#                     sleep_duration = random.randint(1, 3)
#                     time.sleep(sleep_duration)
#                     if self.proxies:
#                         self.proxies = self.proxyUtil.get_proxy()
#                     continue
#                     # 无内容返回
#                 end_time = time.time()
#                 # print(
#                     # f"请求成功: {url} 用时{end_time - start_time:.2f}秒 状态码: {res.status_code} 内容大小: {len(res.content)}字节 重试次数: {attempt + 1}")
#                 return res
#             except Exception as e:
#                 # print(f"第{attempt + 1}次请求失败: {url} 错误: {e}")
#                 sleep_duration = random.randint(1, 4)
#                 time.sleep(sleep_duration)
#                 if self.proxies:
#                     self.proxies = self.proxyUtil.get_proxy()
#                 if attempt  >= retry_count / 2 :
#                     return None
#         return None

# curl_cffi 协程 爬取大量url
# ---------------------------------------------------------------------------
# --- 全新：使用 curl_cffi 的多线程请求处理器 (稳定可靠) ---
# ---------------------------------------------------------------------------
class CurlCffiThreadRequestHandler:
    """
    使用 curl_cffi 和多线程实现的高性能、高伪装性的Web请求类。
    该模型稳定、可靠，适合绝大多数爬虫场景。
    """

    def __init__(self, **kwargs):
        """
        初始化多线程请求处理器。

        :key test_url (str, optional): 用于初始化代理。
        :key max_workers (int, optional): 最大线程数。默认为 10。
        :key headers (dict, optional): 全局请求头。
        :key cookies (dict, optional): 全局Cookies。
        :key method (str, optional): 默认请求方法 (GET/POST)。默认为 'GET'。
        :key impersonate (str, optional): 模拟的浏览器指纹。默认为 'chrome120'。
        """
        test_url = kwargs.get('test_url')
        self.max_workers = kwargs.get('max_workers', 10)
        self.proxy_util = ProxyUtil(test_url) if test_url else None
        self.headers = kwargs.get('headers')
        self.cookies = kwargs.get('cookies')
        self.method = kwargs.get('method', 'GET').upper()
        self.impersonate = kwargs.get('impersonate', 'chrome120')

        # --- 证书路径处理 ---
        self.ca_bundle_path = os.getenv("JIMMYSPIDER_SSL_CERT_FILE", certifi.where())

    def fetch(self, url: str, retry_count: int = 3, **kwargs) -> Dict[str, Optional[str]]:
        """
        获取单个URL的内容，包含重试和代理逻辑。
        这是一个标准的阻塞函数，适合在线程中运行。
        """
        proxy = self.proxy_util.get_proxy() if self.proxy_util else None

        for attempt in range(retry_count):
            try:
                request_kwargs = {
                    'headers': self.headers,
                    'cookies': self.cookies,
                    'proxies': {"http": proxy, "https": proxy} if proxy else None,
                    'timeout': 20,
                    'impersonate': self.impersonate,
                    'verify': self.ca_bundle_path,
                    **kwargs
                }

                if self.method == 'POST':
                    response = curl_cffi_requests.post(url, **request_kwargs)
                else:
                    response = curl_cffi_requests.get(url, **request_kwargs)

                response.raise_for_status()
                return {url: response.text}

            except Exception as e:
                # print(f"线程 {attempt + 1} 请求失败: {url} 错误: {e}")
                if self.proxy_util:
                    proxy = self.proxy_util.get_proxy()  # 更换代理
                if attempt == retry_count - 1:
                    return {url: None}
        return {url: None}

    def fetch_all(self, url_list: List[str], **kwargs) -> Dict[str, Optional[str]]:
        """
        使用线程池并发获取所有URL的内容，并显示进度。
        """
        results = {}
        total_urls = len(url_list)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 使用字典将 future 映射回 url，便于调试
            future_to_url = {executor.submit(self.fetch, url, **kwargs): url for url in url_list}

            completed_count = 0
            for future in as_completed(future_to_url):
                try:
                    result = future.result()
                    results.update(result)
                except Exception as e:
                    url = future_to_url[future]
                    # print(f"URL {url} 在线程中执行时发生严重错误: {e}")
                    results.update({url: None})

                completed_count += 1
                percentage = (completed_count / total_urls) * 100
                print(
                    f"\r多线程请求进度: {completed_count}/{total_urls} ({percentage:.2f}%)",
                    end=""
                )
                sys.stdout.flush()

        print("\n所有多线程任务处理完成。")
        return results

class CurlCffiAsyncRequestHandler:
    """
    使用 asyncio 和 curl_cffi.aio 实现的高性能、高伪装性的异步Web请求类。
    注意：IDE的静态分析器可能无法正确识别AsyncSession，这是一个已知的无害误报。
    """

    def __init__(self, **kwargs):
        test_url = kwargs.get('test_url')
        max_workers = kwargs.get('max_workers', 10)
        self.proxy_util = ProxyUtil(test_url) if test_url else None
        self.headers = kwargs.get('headers')
        self.cookies = kwargs.get('cookies')
        self.method = kwargs.get('method', 'GET').upper()
        self.impersonate = kwargs.get('impersonate', 'chrome120')
        self.semaphore = asyncio.Semaphore(max_workers)
        import os
        self.ca_bundle_path = os.getenv("JIMMYSPIDER_SSL_CERT_FILE", certifi.where())

    async def fetch_one(self, session: curl_cffi.AsyncSession, url: str, retry_count: int = 3, **kwargs) -> Dict[
        str, Optional[str]]:
        async with self.semaphore:
            proxy = self.proxy_util.get_proxy() if self.proxy_util else None
            for attempt in range(retry_count):
                try:
                    request_kwargs = {
                        'headers': self.headers,
                        'cookies': self.cookies,
                        'proxies': {"http": proxy, "https": proxy} if proxy else None,
                        'timeout': 20,
                        'impersonate': self.impersonate,
                        'verify': self.ca_bundle_path,
                        **kwargs
                    }
                    if self.method == 'POST':
                        response = await session.post(url, **request_kwargs)
                    else:
                        response = await session.get(url, **request_kwargs)
                    response.raise_for_status()
                    return {url: response.text}
                except Exception:
                    if self.proxy_util:
                        proxy = self.proxy_util.get_proxy()
                    if attempt == retry_count - 1:
                        return {url: None}
                    await asyncio.sleep(1 + attempt)
            return {url: None}

    async def _fetch_all(self, url_list: List[str], **kwargs) -> Dict[str, Optional[str]]:
        results = {}
        completed_count = 0
        failed_count = 0
        total_urls = len(url_list)
        tasks = [self.fetch_one(url, **kwargs) for url in url_list]
        for future in asyncio.as_completed(tasks):
            # 输出处理的进度
            result = await future
            results.update(result)
            if result.get(result.keys()[0]):
                completed_count += 1
            else:
                failed_count += 1
            print(f"\r进度: {completed_count}/{total_urls} 成功: {completed_count} 失败: {failed_count}", end="")
            sys.stdout.flush()
        print(f"\r所有Curl_cffi 异步请求任务处理完成。共:{total_urls} 成功: {completed_count} 失败: {failed_count}")
        return results

    def fetch_all(self, url_list: List[str], **kwargs) -> Dict[str, Optional[str]]:
        """
        并发获取所有URL的内容，并使用 rich 库显示精美的进度条。
        注意：这是一个阻塞函数，适合在线程中运行。
        """
        results = asyncio.run(self._fetch_all(url_list, **kwargs))
        return results

# ---------------------------------------------------------------------------
# --- 调度器工具: 请求去重 + 域级速率限制 (迁移自 scheduler 研究) ---
# --- 源: spider research/爬虫架构/scheduler/scrapy_sched/scheduler.py   ---
# ---     spider research/爬虫架构/scheduler/aiospider_sched/scheduler.py ---
# ---------------------------------------------------------------------------

class RFPDupeFilter:
    """
    请求指纹去重器 (Scrapy RFPDupeFilter 风格)

    - 对每个请求计算 SHA1 指纹 (url + method + body)
    - 已见过的指纹被过滤 (request_seen 返回 True)
    - 默认容量 100000，满时清理一半，防止内存溢出

    用法:
        dupe = RFPDupeFilter()
        if not dupe.request_seen(url):
            # 处理新请求
        # 也兼容 duck-typed 请求对象 (req.url / req.method / req.body)

    迁移说明: 原实现接收 scheduler 的 Request 对象，这里改为接收 url 字符串，
    同时保留对带 url/method/body 属性的对象的兼容。
    """

    def __init__(self, max_size: int = 100000):
        self.fingerprints: set = set()
        self.max_size = max_size

    def fingerprint(self, url: str, method: str = "GET", body=None) -> str:
        """计算请求指纹 (SHA1 of url + method + body)"""
        fp = hashlib.sha1()
        fp.update(url.encode("utf-8"))
        fp.update(method.encode("utf-8"))
        # body 也参与去重
        if body:
            if isinstance(body, str):
                body = body.encode("utf-8")
            fp.update(body)
        return fp.hexdigest()

    def request_seen(self, url, method: str = "GET", body=None) -> bool:
        """检查请求是否已见过（已见过返回 True）"""
        if not isinstance(url, str):
            # 兼容 duck-typed 请求对象: request_seen(req)
            method = getattr(url, "method", method)
            body = getattr(url, "body", body)
            url = url.url
        fp = self.fingerprint(url, method, body)
        if fp in self.fingerprints:
            return True
        # 防止内存溢出: 满时清理一半
        if len(self.fingerprints) >= self.max_size:
            logging.getLogger(__name__).warning(f"去重集已满({self.max_size})，清理一半")
            to_remove = list(self.fingerprints)[:len(self.fingerprints) // 2]
            for r in to_remove:
                self.fingerprints.discard(r)
        self.fingerprints.add(fp)
        return False

    def clear(self):
        self.fingerprints.clear()

    def __len__(self):
        return len(self.fingerprints)


class DomainRateLimiter:
    """
    域级速率限制 (每域名独立锁 + 最后请求时间戳)

    - wait():        同步版本 (threading.Lock 实现，线程安全)
    - wait_async():  异步版本 (asyncio.Lock 实现，不阻塞事件循环)
    - 每域名独立计时，互不影响；同步/异步共享同一计时表

    用法:
        limiter = DomainRateLimiter(default_delay=1.0)

        # 同步
        limiter.wait(url)
        resp = requests.get(url)

        # 异步
        await limiter.wait_async(url)
        resp = await session.get(url)

    迁移说明: 原实现 (aiospider_sched) 仅提供 async 版，参数为 Request 对象；
    这里改为 url 字符串 + 新增 threading 同步版。
    """

    def __init__(self, default_delay: float = 1.0):
        self.default_delay = default_delay
        # 同步: 域名 → threading.Lock; _meta_lock 保护字典结构
        self._last_request: Dict[str, float] = {}
        self._meta_lock = threading.Lock()
        self._domain_locks: Dict[str, threading.Lock] = {}
        # 异步: 域名 → asyncio.Lock
        self._async_locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _get_domain(url) -> str:
        """从 URL 提取域名 (兼容带 .url 属性的请求对象)"""
        from urllib.parse import urlparse
        if not isinstance(url, str):
            url = url.url
        return urlparse(url).netloc or "unknown"

    def wait(self, url, delay: float = None) -> None:
        """同步等待: 距上次同域名请求不足 delay 秒则休眠补齐"""
        domain = self._get_domain(url)
        d = delay if delay is not None else self.default_delay
        with self._meta_lock:
            lock = self._domain_locks.setdefault(domain, threading.Lock())
        with lock:
            now = time.time()
            last = self._last_request.get(domain, 0)
            wait_time = d - (now - last)
            if wait_time > 0:
                time.sleep(wait_time)
            self._last_request[domain] = time.time()

    async def wait_async(self, url, delay: float = None) -> None:
        """异步等待: 距上次同域名请求不足 delay 秒则 asyncio.sleep 补齐"""
        domain = self._get_domain(url)
        d = delay if delay is not None else self.default_delay
        lock = self._async_locks.get(domain)
        if lock is None:
            lock = asyncio.Lock()
            self._async_locks[domain] = lock
        async with lock:
            now = time.time()
            last = self._last_request.get(domain, 0)
            wait_time = d - (now - last)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_request[domain] = time.time()

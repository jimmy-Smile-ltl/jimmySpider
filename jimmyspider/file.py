import asyncio
import concurrent.futures
import mimetypes
import os
import pathlib
import re
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Optional, Union
from urllib.parse import unquote, urlparse

import aiohttp
import json
import requests
from pathvalidate import sanitize_filename

from jimmyspider.config import get_config
from jimmyspider.request import SingleRequestHandler
from jimmyspider.request import CurlRequestHandler
from jimmyspider.proxy import ProxyUtil
from curl_cffi.requests import AsyncSession
from curl_cffi import CurlOpt
# from pathlib import Path
#
# # 使用 .expanduser() 展开波浪号
# path = Path("~/spider_files/").expanduser()
#
# if path.exists():
#     print(f"找到了：{path}")

# 不`设置timeout 大文件老是直接断了 改成文件传输数度 小于 1kb / s 然后重试
class FileDownloader:
    """
    一个通用的文件上传类，支持多线程和异步模式。
    可以从URL列表下载指定类型的文件（包括文档和图片），并上传到file。
    """

    def __init__(self,
                 pro_name: str,
                 mode: str = 'thread',
                 max_workers: int = 20,
                 file_url_field: str = "file_url",
                 method: str = 'GET',
                 strict_mime_check: bool = False,
                 test_url: Optional[str|bool] = None,
                 default_type: str = ".pdf",
                 curl = False,
                 use_clash_pool = False,
                 **kwargs):
        """
        初始化文件下载器。

        :param file_name: file上的目标目录名。 可以是加/ 的 比如 /daily/ 或者 daily/RMRB
        :param mode: 工作模式, 'thread' (多线程) 或 'async' (异步)。
        :param max_workers: 最大并发工作线程数。
        :param allowed_extensions: 允许的文件扩展名列表。如果为None, 默认支持多种文档和图片格式。
        :param strict_mime_check: 是否进行严格的MIME类型检查。默认为True。
        :param test_url: 用于测试的URL (传递给SingleRequestHandler)。 是否使用代理
        :param headers: 自定义请求头。
        :param curl ： TLS 指纹伪装
        :param kwargs: 其他传递给 aiohttp 或 requests 的参数。
        """
        self.file_url_field = file_url_field
        self.default_type = default_type
        import os
        from pathlib import Path
        self.pro_name = Path(pro_name).name
        config = get_config()
        self.file_path = os.path.join(config.DATA_DIR, pro_name)
        # 创建路径
        if not os.path.exists(self.file_path):
            os.makedirs(self.file_path,exist_ok=True)

        self.file_save_path_name ="files_by_date"
        if not os.path.exists(self.file_path+f"/{self.file_save_path_name}"):
            os.makedirs(self.file_path+f"/{self.file_save_path_name}",exist_ok=True)
        if mode not in ['thread', 'async']:
            raise ValueError("mode 必须是 'thread' 或 'async'")
        self.mode = mode
        self.kwargs = kwargs
        self.max_workers = max_workers
        self.strict_mime_check = strict_mime_check
        self.curl = curl
        self.use_clash_pool =use_clash_pool
        if curl:
            self.handler = CurlRequestHandler(
                test_url=test_url,
                use_clash_pool =use_clash_pool
            )
        else:
            self.handler = SingleRequestHandler(
                test_url=test_url,
                use_clash_pool =use_clash_pool
            )
        self.method = method.upper()
        if test_url:
            self.proxy_util = ProxyUtil(test_url=test_url)
            self.proxies = self.proxy_util.get_proxy()
        else:
            self.proxy_util = None
            self.proxies = None



        # 定义允许的文件类型和对应的MIME类型
        self.allowed_extensions: List[str] = [
            # 文档
            '.pdf', '.docx', '.doc', '.ppt', '.pptx', '.xls', '.xlsx', '.txt', '.csv', '.rtf', '.epub',
            # 图片
            '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.tiff',
            # 压缩包
            '.zip', '.rar', '.7z', '.tar', '.gz',
            # 音视频
            '.mp3', '.wav', '.mp4', '.mov', '.avi', '.mkv',
        ]

        self.mime_map = {
            # 文档
            '.pdf': 'application/pdf',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.doc': 'application/msword',
            '.txt': 'text/plain',
            # 图片
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp',
        }
        if "headers"  in self.kwargs:
            self.headers =  self.kwargs.pop("headers")
        else:
            self.headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

    def get_today_path(self) -> str:
        today_str = time.strftime("%Y-%m-%d")
        today_path = os.path.join(self.file_path, self.file_save_path_name , today_str)
        if not os.path.exists(today_path):
            os.makedirs(today_path, exist_ok=True)
        return today_path

    def start(self, id_urls: List[str], show_progress: bool = True,default_type = ".pdf"):
        """
        根据设定的模式启动上传任务。
        :param id_urls: 包含文件URL的列表。
        :param show_progress: 是否显示进度条。
        """
        if default_type:
            self.default_type = default_type

        if len(id_urls) > 0 and isinstance(id_urls[0], dict):
            with open(self.file_path+"/data.jsonl", 'a+',encoding="utf8") as f:
                for article_data in id_urls:
                    if isinstance(article_data, dict):
                        one_line = json.dumps(article_data, ensure_ascii=False,indent=0)
                        f.write(str(one_line) + "\n")

        if self.mode == 'thread':
            self._start_thread(id_urls, show_progress=show_progress)
        elif self.mode == 'async':
            asyncio.run(self._start_async(id_urls, show_progress=show_progress ))
        else:
            print("不支持的模式")

    def start_async(self, id_urls: List[str] | List[dict], show_progress: bool = True, default_type=".pdf"):
        """
        启动异步上传任务。
        curl=True  → 使用 curl_cffi.AsyncSession（绕过 TLS 指纹检测）
        curl=False → 使用 aiohttp.ClientSession（默认高性能模式）
        """
        if type(self.proxies) is dict:
            self.proxies = self.proxies.get("http")
        if default_type:
            self.default_type = default_type
        if not id_urls:
            print("没有需要处理的文件链接。")
            return None

        if isinstance(id_urls, list) and isinstance(id_urls[0], dict):
            if self.curl:
                # ── curl_cffi 异步模式 ──────────────────────────
                upload_results = asyncio.run(self._start_async_curl(id_urls, show_progress=show_progress))
            else:
                # ── aiohttp 异步模式（原有逻辑）─────────────────
                upload_results = asyncio.run(self._start_async(id_urls, show_progress=show_progress))
            return upload_results
        else:
            print("输入的文件链接列表格式不正确。请提供一个字符串列表或字典列表。")
            print(id_urls)
            return None

    def start_thread(self, id_urls: List[str] | List[dict], show_progress: bool = True,default_type=".pdf"):
        if default_type:
            self.default_type = default_type
        if not id_urls:
            print("没有需要处理的文件链接。")
            return {}
        elif isinstance(id_urls, list) and isinstance(id_urls[0], dict):
            # 如果是字符串列表，直接调用线程处理
            upload_results = self._start_thread(id_urls, show_progress=show_progress)
            return upload_results
        else:
            print("输入的文件链接列表格式不正确。请提供一个字符串列表或字典列表。")
            print(id_urls)
            return None


    def _start_thread(self, id_urls: List[str] | List[dict], show_progress: bool = True):
        """
        使用线程池并发处理所有文件链接。
        """
        self._total_files = len(id_urls)
        self._completed_count = 0
        self._error_count = 0  # 新增错误计数
        self._progress_lock =threading.Lock()
        result_list = []
        if self._total_files == 0:
            if show_progress:
                print("没有需要处理的文件链接。")
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_url  = {executor.submit(self._handle_one_file_speed, id_url ): id_url for id_url in id_urls}
            for future in concurrent.futures.as_completed(future_to_url):
                id_url = future_to_url[future]
                try:
                    file_path = future.result()  # 尝试获取结果，如果 _handle_one_file 抛出异常，这里会捕获
                    id_url["file_path"] = file_path
                    result_list.append(id_url)
                    with self._progress_lock:
                        if file_path:
                            self._completed_count += 1
                        else:
                            self._error_count += 1
                except Exception as e:
                    with self._progress_lock:
                        self._error_count += 1
                    if show_progress:
                        pass
                if show_progress:
                    with self._progress_lock:  # 确保打印是线程安全的
                        percentage = ((self._completed_count + self._error_count) / self._total_files) * 100
                        print(
                            f"\r文件下载 开始处理 {self._total_files} 个文件，使用 {self.max_workers} 个线程。  进度: {self._completed_count + self._error_count}/{self._total_files} ({percentage:.2f}%) 成功: {self._completed_count} 失败: {self._error_count}",
                            end='\t')
                        sys.stdout.flush()
            print()
            sys.stdout.flush()
            return result_list
        # ── 改写：requests 多线程单文件处理 ───────────────────────────────────────────
    def _handle_one_file_speed(self, id_url: dict) -> str | bool:
            file_url = ""
            try:
                file_url = id_url.get(self.file_url_field, "")
                if not file_url.startswith(("http://", "https://")):
                    print(f"跳过无效URL: {file_url}")
                    return False
                try:
                    result = self._smart_download(
                        url=file_url,
                        total_timeout=60
                    )
                    if result is None:
                        return False
                    content, resp_headers = result

                except TimeoutError as e:
                    print(f"[requests] 速度超时: {file_url}, 错误: {e}")
                    return False
                except Exception as e:
                    print(f"[requests] 请求异常: {file_url},   错误: {e}")
                    return False

                if not content:
                    return False

                # MIME 检查
                if self.strict_mime_check:
                    content_type = resp_headers.get("Content-Type", "").strip().lower()
                    expected_mime = self.mime_map.get(
                        self.get_smart_file_extension(file_url, resp_headers)
                    )
                    if expected_mime and not content_type.startswith(expected_mime):
                        if not content_type.startswith("application/octet-stream"):
                            print(f"MIME不匹配: {file_url}, Content-Type: {content_type}")
                            return False

                file_path = self.get_file_path(
                    id=id_url.get("_id", ""),
                    url=file_url,
                    headers=resp_headers,
                    default_type=self.default_type,
                )
                with open(file_path, "wb") as f:
                    f.write(content)
                return file_path

            except Exception as e:
                print(f"[requests] 处理文件 {file_url} 时发生异常: {e}")
                return False


    def _handle_one_file(self, id_url:dict) -> str|bool:
        """
        处理单个文件链接：下载、验证、上传。
        这个方法在多线程中执行，不应直接抛出异常，而是返回结果或记录内部状态。
        """
        try:
            file_url = id_url.get(self.file_url_field,"")
            # 1. 检查URL协议和文件扩展名
            if not file_url.startswith(('http://', 'https://')):
                print(f"跳过无效URL: {file_url}")
                return False
            # 2. 下载文件，带重试逻辑
            response = None
            for _ in range(3):  # 重试3次
                # kwargs 会传递 timeout 等参数给 requests.get/post
                response = self.handler.fetch(file_url, method='GET', headers=self.headers, retry_count= 3 , **self.kwargs)
                if not response or response.status_code != 200 or response.text.strip().startswith("<!DOCTYPE html>"):
                    continue
                else:
                    break
            if not response or response.status_code != 200:
                print(f"下载失败或无效响应: {file_url}, 状态码: {response.status_code if response else 'N/A'}")
                return False

            # 3. 验证下载内容是否有效
            if self.strict_mime_check:
                content_type = response.headers.get('Content-Type', '').strip().lower()
                # 注意：这里 original code uses aiohttp.ClientResponse.headers, but response is requests.Response.
                # headers.get() is compatible, but type hint should be Dict[str, str].
                expected_mime = self.mime_map.get(self.get_smart_file_extension(file_url, response.headers))
                if expected_mime and not content_type.startswith(expected_mime):
                    if not content_type.startswith('application/octet-stream'):
                        print(f"MIME类型不匹配或无效: {file_url}, Content-Type: {content_type}, 期望: {expected_mime}")
                        return False

            if not response.content or response.headers.get("content-length") == '0':
                print(f"下载内容为空: {file_url}")
                return False

            file_path = self.get_file_path(id=id_url.get("_id",""), url=file_url, headers=response.headers, default_type= self.default_type)
            with open(file_path, 'wb') as f:
                f.write(response.content)
            return file_path

        except Exception as e:
            # 捕获所有其他异常并记录
            print(f"处理文件 {file_url} 时发生异常: {e}")
            return False

    async def _start_async(
            self,
            id_urls: List[dict],
            show_progress: bool = True,
    ) -> List[Dict[str, Union[str, bool, None]]]:
        """
        共享 session + TCPConnector 连接池复用，最优并发性能。
        """
        total_files = len(id_urls)
        if total_files == 0:
            if show_progress:
                print("没有需要处理的文件链接。")
            return []

        sem = asyncio.Semaphore(self.max_workers)
        completed_count = 0
        error_count = 0
        result_list = []

        connector = aiohttp.TCPConnector(
            ssl=False,  # 关闭 SSL 验证，避免证书问题
            limit=self.max_workers * 2,  # 连接池上限，留出重试余量
            limit_per_host=self.max_workers,  # 单域名连接上限
            ttl_dns_cache=300,  # DNS 缓存 5 分钟
            enable_cleanup_closed=True,  # 自动清理关闭的连接
        )

        async with aiohttp.ClientSession(
                headers=self.headers,
                connector=connector,
                read_bufsize=1024 * 1024,  # 读缓冲区 1MB，适合大文件并发
        ) as session:
            tasks = {
                asyncio.create_task(
                    self._handle_one_file_async_speed(id_url, session, sem)
                ): id_url
                for id_url in id_urls
            }

            for future in asyncio.as_completed(tasks):
                try:
                    result = await future
                    if result:
                        result_list.append(result)
                        completed_count += 1
                    else:
                        error_count += 1
                except Exception:
                    error_count += 1
                finally:
                    if show_progress:
                        percentage = ((completed_count + error_count) / total_files) * 100
                        print(
                            f"\r[aiohttp] 进度: {completed_count + error_count}/{total_files} "
                            f"({percentage:.2f}%) 成功: {completed_count} 失败: {error_count} "
                            f"最大并发: {self.max_workers}",
                            end="\t",
                        )
                        sys.stdout.flush()

        if show_progress:
            print(
                f"\n[aiohttp] 下载完成 路径: {self.file_path}  "
                f"成功: {completed_count}/{total_files}  失败: {error_count}/{total_files}"
            )
        return result_list

    def _write_file(self, file_path: str, content: bytes):
        with open(file_path, "wb") as f:
            f.write(content)

    async def _handle_one_file_async_speed(
            self,
            id_url: dict,
            session: aiohttp.ClientSession,
            sem: asyncio.Semaphore,
    ) -> dict | bool | None:
        """
        单文件下载：
        - proxy 局部变量，避免多协程并发修改 self.proxies 竞态
        - 异步写文件，不阻塞事件循环
        - 分类捕获异常，payload 不完整单独处理
        """
        async with sem:
            file_url = ""
            try:
                file_url = id_url.get(self.file_url_field, "")
                if not file_url.startswith(("http://", "https://")):
                    print(f"跳过无效URL: {file_url}")
                    return False

                # ✅ 局部持有 proxy，重试时只更新本协程的副本，不污染其他协程
                proxy = self.proxies
                retry_count = 6

                for retry in range(retry_count):
                    try:
                        result = await self._smart_download_async(
                            session=session,
                            url=file_url,
                            proxy=proxy,
                            sock_read=60,
                        )

                        content, resp_headers = result
                        if not content:
                            await asyncio.sleep(2)
                            continue

                        file_path = self.get_file_path(
                            id=id_url.get("_id", ""),
                            url=file_url,
                            headers=resp_headers,
                            default_type=self.default_type,
                        )

                        # ✅ 异步写文件，释放事件循环给其他协程
                        await asyncio.to_thread(self._write_file, file_path, content)

                        id_url["file_path"] = file_path
                        return id_url

                    except (aiohttp.ClientPayloadError, aiohttp.ClientSSLError) as e:
                        # payload 不完整，等久一点
                        wait = 5 + retry * 2
                        # print(    f"[aiohttp] 传输不完整: {file_url}, 重试({retry + 1}/{retry_count}), 等待{wait}s, 错误: {e}")
                        await asyncio.sleep(wait)

                    except aiohttp.ClientResponseError as e:
                        # HTTP 错误码（4xx/5xx），部分不需要重试
                        if e.status in (403, 404, 410):
                            print(f"[aiohttp] HTTP {e.status} 不重试，放弃: {file_url}")
                            return False
                        wait = 2
                        # print(f"[aiohttp] HTTP错误: {file_url}, 重试({retry + 1}/{retry_count}), 等待{wait}s, 错误: {e}")
                        await asyncio.sleep(wait)

                    except Exception as e:
                        wait = 2
                        # print(f"[aiohttp] 异常: {file_url}, 重试({retry + 1}/{retry_count}), 等待{wait}s, 错误: {e}")
                        # ✅ 只更新本协程局部 proxy，不影响其他协程
                        if self.proxy_util:
                            new_proxy = self.proxy_util.get_proxy()
                            proxy = (
                                new_proxy.get("http")
                                if isinstance(new_proxy, dict)
                                else new_proxy
                            )
                        await asyncio.sleep(wait)

                print(f"[aiohttp] 超出重试次数，放弃: {file_url}")
                return False

            except Exception as e:
                print(f"[aiohttp] 处理文件 {file_url} 时发生异常: {e}")
                return False


    async def _handle_one_file_async(self, id_url: dict, session: aiohttp.ClientSession, sem: asyncio.Semaphore,
                                ) -> str | bool | dict | None:
        """
        异步处理单个文件链接。
        """
        async with sem:
            try:
                file_url = id_url.get(self.file_url_field,"")
                if not file_url.startswith(('http://', 'https://')):
                    print(f"跳过无效URL: {file_url}")
                    return False
                retry_count = 6
                for retry in range(retry_count):
                    try:
                        timeout = aiohttp.ClientTimeout(total=60, connect=20, sock_read=20)
                        async with session.get(file_url, timeout=timeout, **self.kwargs, proxy=self.proxies) as resp:
                            resp.raise_for_status()
                            if retry < int(retry_count / 2) + 1:
                                if resp.status != 200:
                                    time_sleep = 2 + retry_count % 3
                                    await asyncio.sleep(time_sleep)  # 等待 retry 秒后重试
                                    self.proxies = self.proxy_util.get_proxy()
                                    continue
                                content = await resp.read()
                            else:
                                # 循环读取数据块并存入列表
                                byte_chunks = []
                                chunk_size = 1024 * 500  # 100kb 每个数据块的大小
                                async for chunk in resp.content.iter_chunked(chunk_size):
                                    byte_chunks.append(chunk)
                                # 使用 b''.join() 高效拼接所有字节块
                                content = b"".join(byte_chunks)
                            resp_headers = resp.headers
                            if not content:
                                continue
                            file_path = self.get_file_path(id=id_url.get("_id",""),url=file_url, headers=resp_headers, default_type=self.default_type)
                            with open(file_path, 'wb') as f:
                                f.write(content)
                            id_url["file_path"] = file_path
                            return id_url
                    except Exception as e:
                        time_sleep = 2  + retry_count % 3
                        print(f"异步处理文件失败: {file_url}, 重试次数: {retry + 1}, 错误: {e}. 将在 {time_sleep} 秒后重试.")
                        await asyncio.sleep(time_sleep)  # 等待 retry 秒后重试
                        continue
                else:
                    print(f"异步处理文件失败: {file_url}, 重试次数: {retry + 1}")
                    return False
            except Exception as e:
                # 重新抛出异常
                print(f"异步处理文件时发生异常: {file_url}, 错误: {e}")
                return False

    def get_file_name(self, url: str, headers: Optional[Union[dict, ]]) -> Optional[str]:
        # 文件类型
        file_extension = self.get_smart_file_extension(url, headers)
        file_extension = file_extension.replace(";","")
        if file_extension.lower() == ".svg":
            file_extension = ".html"
        if not file_extension:
            print(f"无法从URL '{url}' 获取有效的文件扩展名。")
            return None
        file_name = self.get_smart_filename(url,headers= headers)
        if not file_name:
            print(f"无法从URL '{url}' 获取有效的文件名基础。")
            return None
        # 返回一个安全的文件名
        return sanitize_filename(f"{file_name}{file_extension}")

    def get_smart_file_extension(
            self,
            url: str,
            headers: Optional[Dict[str, str]] = None,
            default_type: str = ".pdf"
    ) -> Optional[str]:
        """
        [全新设计] 根据URL和HTTP响应头，智能地判断并返回一个有效的文件扩展名。

        该函数会按照以下优先级进行判断，一旦找到有效的扩展名就会立即返回：
        1.  **从URL路径中直接解析** (e.g., /path/to/file.pdf)。
        2.  **从URL路径的最后部分解析** (用于干净URL, e.g., /path/to/id/pdf)。
        3.  **从响应头的 'Content-Disposition' 中解析**。
        4.  **从响应头的 'Content-Type' 中使用正则表达式模式匹配**。
        5.  **从响应头的 'Content-Type' 中使用内置库进行猜测**。

        :param url: 文件的来源URL。
        :param headers: (可选) requests库返回的响应头字典。
        :return: 一个小写的、以点开头的文件扩展名 (如 '.pdf')，如果无法判断则返回 None。

        """

        # ---  没有headers 使用url ---
        def _is_valid_extension(ext: str) -> bool:
            """
            检查扩展名是否看起来像一个有效的文件扩展名。
            """
            return ext in self.allowed_extensions

        if not headers:
            if not url or not isinstance(url, str):
                return None

            # --- 阶段一：优先从URL解析 ---
            try:
                path = Path(urlparse(url).path)

                # 1a. 尝试从常规路径后缀获取
                ext = path.suffix.lower()
                if _is_valid_extension(ext):
                    return ext
                else:
                    return default_type  # 默认返回.pdf

                # 1b. 尝试从干净URL的最后一个路径段获取
                path_segments = [seg for seg in path.parts if seg != '/']
                if path_segments:
                    # 假设最后一个路径段是文件类型
                    last_segment_ext = f".{path_segments[-1].lower()}"
                    # 简单的验证，确保它看起来像一个文件后缀 (例如，长度不超过5)
                    if 1 < len(last_segment_ext) <= 5:
                        return last_segment_ext
            except Exception as e:
                print(f"解析URL '{url}' 时发生错误: {e}")
                return default_type  # 默认返回.pdf
        else:
            # 2a. 尝试从 Content-Disposition 头获取
            content_disposition = headers.get('content-disposition')
            if content_disposition:
                match = re.search(r"filename\*=UTF-8''(.+)", content_disposition, re.IGNORECASE)
                if not match:
                    match = re.search(r'filename="?([^"]+)"?', content_disposition, re.IGNORECASE)
                if match:
                    filename = unquote(match.group(1).strip("'\" "))
                    # "热点周报2025年第26期（总149期）-水印.pdf;'" 会多一个;
                    ext = Path(filename).suffix.lower()
                    if ext:
                        return ext.replace(";","")

            # 2b. 尝试从 Content-Type 头推断
            content_type = headers.get('Content-Type', '').split(';')[0].strip().lower()
            if content_type:
                # 定义MIME类型匹配模式
                mime_patterns = [
                    (r'application/(x-)?pdf', '.pdf'),
                    (r'application/vnd\.openxmlformats-officedocument\.wordprocessingml\.document', '.docx'),
                    (r'application/msword', '.doc'),
                    (r'text/plain', '.txt'),
                    (r'image/jpeg', '.jpg'),
                    (r'image/png', '.png'),
                    (r'image/gif', '.gif'),
                ]
                for pattern, extension in mime_patterns:
                    if re.match(pattern, content_type):
                        return extension

                # 使用内置库作为最后备选
                guessed_ext = mimetypes.guess_extension(content_type)
                if guessed_ext:
                    return guessed_ext.lower()

            return default_type  # 默认返回.pdf




    # 文件名
    def get_smart_filename(self, url: str,
                           headers: Optional[Union[dict,None]]=None) -> Optional[str]:
        """
        [最终改良版] 从给定的URL中，智能地提取一个有意义的文件名基础（不含扩展名）。

        该函数会按照以下优先级进行判断：
        1.  优先从URL中提取DOI (e.g., 10.xxxx/xxxxx)。
        2.  对URL路径进行预处理，移除像 /pdf /download 这样的尾部动作词。
        3.  对预处理后的路径，智能判断有意义的部分。
            -   特别处理 `.../some-id/v1` 这样的结构。
            -   处理普通的文件名或ID。
        4.  最后，提供一个基于域名的备用方案。

        :param url: 文件的来源URL。
        :return: 一个字符串形式的文件名基础，如果URL无效则返回None。
        """
        if headers:
            # 2a. 尝试从 Content-Disposition 头获取
            content_disposition = headers.get('content-disposition')
            if content_disposition:
                match = re.search(r"filename\*=UTF-8''(.+)", content_disposition, re.IGNORECASE)
                if not match:
                    match = re.search(r'filename="?([^"]+)"?', content_disposition, re.IGNORECASE)
                if match:
                    filename = unquote(match.group(1).strip("'\" "))
                    if filename:
                        filename = Path(filename).stem
                        return filename

        if not url or not isinstance(url, str):
            return None

        try:
            # 为了处理 '         https://...' 这样的情况，先去除首尾空格
            url = url.strip()
            # 1. 优先尝试从URL中提取DOI作为文件名基础
            # 这个正则表达式可以匹配大多数DOI格式
            doi_pattern = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
            doi_match = doi_pattern.search(url)
            if doi_match:
                # 提取DOI主体部分，并替换特殊字符
                doi_str = doi_match.group(1)
                # 如果DOI后面跟着/pdf, /fulltext等，移除它们
                for suffix in ['/pdf', '/fulltext', '/view', '/download']:
                    if doi_str.lower().endswith(suffix):
                        doi_str = doi_str[:-len(suffix)]
                        break
                return doi_str.replace('/', '_').replace('.', '_')

            # 如果没有DOI，则解析URL路径
            parsed_url = urlparse(url)
            path = Path(parsed_url.path)

            # 将路径转为列表，方便操作
            path_segments = [seg for seg in path.parts if seg and seg != '/']

            # 2. 预处理路径：移除尾部的通用动作词
            action_words = ['download', 'pdf', 'fulltext', 'view']
            if path_segments and path_segments[-1].lower() in action_words:
                path_segments.pop()  # 移除最后一个元素

            if not path_segments:
                return parsed_url.netloc.replace('.', '_')

            # 3. 对预处理后的路径进行智能判断

            # 针对 .../14-681/v1 这样的结构
            # 如果最后一个段看起来像版本号 (v1, v2 ...)，就和前一个段合并
            if len(path_segments) >= 2 and re.match(r'(v\d+|^image|^pic)', path_segments[-1], re.IGNORECASE) or len(path_segments[-1])<20:
                base_name = f"{path_segments[-2]}_{path_segments[-1]}"
                return base_name.replace('.', '_')

            # 对于其他情况，我们认为最后一个（或预处理后剩下的最后一个）路径段最有意义
            # 这能正确处理 https://osf.io/5ayfm_v1/ 和其他常规URL
            # 也包括 /articles/12345 这样的情况
            # 同时，也处理了带有文件扩展名的情况，因为path.stem会自动移除后缀
            # 例如 /file.v1.pdf -> stem是 'file.v1'
            last_segment_path = Path(path_segments[-1])
            if last_segment_path.suffix:
                return last_segment_path.stem.replace('.', '_')

            return path_segments[-1].replace('.', '_')

        except Exception:
            # 捕获所有可能的解析错误，返回None
            return None
    # file_path
    def get_file_path(self, id: str, url="", headers: Optional[Union[dict,None]] = None,
                      default_type: str = ".pdf"):
        """
        从URL中提取file路径，默认使用file_name作为目录名。
        :param url: 文件的来源URL。
        :param default_type: 如果无法从URL中获取文件扩展名，则使用此默认类型。
        :return: file路径字符串。
        """

        file_name = id
        file_extension = self.get_smart_file_extension(url, headers=headers)
        if not file_extension:
            file_extension = default_type
        if not file_name or not file_extension:
            print(f"无法从URL '{url}' 获取有效的文件名或扩展名。")
            return f"{self.get_today_path()}/{file_name}{self.default_type}"
        return f"{self.get_today_path()}/{file_name}{file_extension}"

    # ── 新增：curl_cffi 异步入口 ────────────────────────────────────────────────
    async def _start_async_curl(
            self,
            id_urls: List[dict],
            show_progress: bool = True,
    ) -> List[Dict[str, Union[str, bool, None]]]:
        """
        使用 curl_cffi.AsyncSession 并发下载文件。
        相比 aiohttp，curl_cffi 可以模拟浏览器 TLS 指纹，适合需要绕过反爬的场景。
        """
        from curl_cffi.requests import AsyncSession  # 延迟导入，未安装时不影响其他模式

        total_files = len(id_urls)
        if total_files == 0:
            if show_progress:
                print("没有需要处理的文件链接。")
            return []

        sem = asyncio.Semaphore(self.max_workers)
        completed_count = 0
        error_count = 0
        result_list = []

        # impersonate="chrome110" 模拟 Chrome TLS 指纹，可按需调整版本
        # async with AsyncSession(
        #         headers=self.headers,
        #         impersonate="chrome110",
        #         proxies={"https": self.proxies, "http": self.proxies} if self.proxies else None,
        #         max_clients=self.max_workers,
        # ) as session:
        #     tasks = {
        #         asyncio.create_task(
        #             self._handle_one_file_async_curl(id_url, session, sem)
        #         ): id_url
        #         for id_url in id_urls
        #     }
            # 去掉 AsyncSession 的 with 块，直接创建 tasks
        tasks = {
            asyncio.create_task(
                self._handle_one_file_async_curl_speed(id_url, sem)  # ← 不再传 session
            ): id_url
            for id_url in id_urls
        }

        for future in asyncio.as_completed(tasks):
            try:
                result = await future
                if result:
                    result_list.append(result)
                    completed_count += 1
                else:
                    error_count += 1
            except Exception:
                error_count += 1
            finally:
                if show_progress:
                    percentage = (completed_count / total_files) * 100
                    print(
                        f"\r[curl_cffi] 文件下载 进度: {completed_count}/{total_files}  失败 {error_count}/{total_files}"
                        f"({percentage:.2f}%)  最大并发 {self.max_workers}",
                        end="\t",
                    )
                    sys.stdout.flush()

        if show_progress:
            print(
                f"\r[curl_cffi] 下载完成 路径 {self.file_path}  "
                f"完成 {completed_count}/{total_files}  失败 {error_count}/{total_files}"
            )
        return result_list


        # ── 改写：curl_cffi 异步单文件处理 ────────────────────────────────────────────
    async def _handle_one_file_async_curl_speed(
                self,
                id_url: dict,
                sem: asyncio.Semaphore,
        ) -> dict | bool | None:
            async with sem:
                file_url = ""
                try:

                    file_url = id_url.get(self.file_url_field, "")
                    if not file_url.startswith(("http://", "https://")):
                        return False

                    retry_count = 5
                    for retry in range(retry_count):
                        try:
                            async with AsyncSession(
                                    headers=self.headers,
                                    impersonate="chrome146",
                                    curl_options=  {
                                            # 核心：速度低于 1KB/s 且持续 30s，才判定为超时
                                            # 文件正常传输时速度远高于此，不会触发
                                            CurlOpt.LOW_SPEED_LIMIT: 1024,   # 1 KB/s
                                            CurlOpt.LOW_SPEED_TIME: 30,      # 持续 30 秒
                                        },
                                    proxies=(
                                            {"https": self.proxies, "http": self.proxies}
                                            if self.proxies else None
                                    ),
                            ) as session:
                                # 在发请求前，直接操作底层 curl handle 设置限速参数

                                resp = await session.get(
                                    file_url,
                                    timeout=600,  # 兜底 10 分钟
                                )

                            if resp.status_code != 200:
                                wait = 2
                                await asyncio.sleep(wait)
                                if self.proxy_util:
                                    new_proxy = self.proxy_util.get_proxy()
                                    self.proxies = (
                                        new_proxy.get("http")
                                        if isinstance(new_proxy, dict)
                                        else new_proxy
                                    )
                                continue

                            content = resp.content
                            if not content:
                                await asyncio.sleep(2)
                                continue

                            resp_headers = dict(resp.headers)
                            file_path = self.get_file_path(
                                id=id_url.get("_id", ""),
                                url=file_url,
                                headers=resp_headers,
                                default_type=self.default_type,
                            )
                            with open(file_path, "wb") as f:
                                f.write(content)
                            id_url["file_path"] = file_path
                            return id_url

                        except Exception as e:
                            wait = 2
                            # print(    f"[curl_cffi] 异常: {file_url}, 重试({retry + 1}/{retry_count}), 等待{wait}s, 错误: {e}")
                            await asyncio.sleep(wait)

                    print(f"[curl_cffi] 超出重试次数，放弃: {file_url}")
                    return False

                except Exception as e:
                    print(f"[curl_cffi] 处理文件 {file_url} 时发生异常: {e}")
                    return False

    async def _handle_one_file_async_curl(
            self,
            id_url: dict,
            sem: asyncio.Semaphore,  # ← 去掉 session 参数
    ) -> dict | bool | None:
        async with sem:
            file_url = id_url.get(self.file_url_field, "")
            if not file_url.startswith(("http://", "https://")):
                return False

            from curl_cffi.requests import AsyncSession

            retry_count = 3
            for retry in range(retry_count):
                try:
                    # ✅ 每个协程独立创建 session，curl handle 互不阻塞
                    async with AsyncSession(
                            headers=self.headers,
                            impersonate="chrome146",
                            proxies={"https": self.proxies, "http": self.proxies} if self.proxies else None,
                    ) as session:
                        # timeout 还不能小了
                        resp = await session.get(file_url, timeout=60)

                    if resp.status_code != 200:
                        wait = 2
                        await asyncio.sleep(wait)
                        if self.proxy_util:
                            new_proxy = self.proxy_util.get_proxy()
                            self.proxies = new_proxy.get("http") if isinstance(new_proxy, dict) else new_proxy
                        continue

                    content = resp.content
                    if not content:
                        await asyncio.sleep(2)
                        continue

                    resp_headers = dict(resp.headers)
                    file_path = self.get_file_path(
                        id=id_url.get("_id", ""),
                        url=file_url,
                        headers=resp_headers,
                        default_type=self.default_type,
                    )
                    with open(file_path, "wb") as f:
                        f.write(content)

                    id_url["file_path"] = file_path
                    return id_url

                except Exception as e:
                    wait = 1
                    # print(f"[curl_cffi] 异常: {file_url}, 重试({retry + 1}/{retry_count}), 等待{wait}s, 错误: {e}")
                    await asyncio.sleep(wait)

            print(f"[curl_cffi] 超出重试次数，放弃: {file_url}")
            return False

        # ── 智能下载核心：requests 同步版 ─────────────────────────────────────────────
    def _smart_download(
                self,
                url: str,
                total_timeout: int = 120,  # 兜底总时长
        ) -> tuple[bytes, dict] | None:
            """
            流式下载 + 智能速度检测（requests版）。
            只要字节还在正常流动就不超时，真正卡死才断开。
            """
            resp = self.handler.fetch(
                url,
                method="GET",
                headers=self.headers,
                # timeout=total_timeout,
                stream = False,   # 妈的 大多 几十 kb/s
                retry_count = 3 ,
                **self.kwargs,
            )
            if not resp or resp.status_code != 200:
                return None

            # 检测是否是 HTML 页面（非文件）
            if not resp.text.strip() or resp.text.strip().startswith("<!DOCTYPE html>") :
                return None

            resp_headers = dict(resp.headers)
            return resp.content, resp_headers

        # ── 智能下载核心：aiohttp 异步版 ──────────────────────────────────────────────
    # async def _smart_download_async(
    #         self,
    #         session: aiohttp.ClientSession,
    #         url: str,
    #         proxy: str | None = None,
    #         sock_read: int = 60,
    # ) -> tuple[bytes, dict]:
    #     """
    #     核心下载方法：
    #     - proxy 作为参数传入，避免多协程竞态
    #     - 流式读取处理 ClientPayloadError
    #     - 有 Content-Length 校验完整性，无则有数据即返回
    #     """
    #     timeout = aiohttp.ClientTimeout(
    #         total=None,  # 不限总时长，避免排队时间计入
    #         connect=30,
    #         sock_connect=30,
    #         sock_read=sock_read,
    #     )
    #
    #     async with session.get(url, timeout=timeout, proxy=proxy, **self.kwargs) as resp:
    #         resp.raise_for_status()
    #         resp_headers = dict(resp.headers)
    #         content_length = resp_headers.get("Content-Length")
    #
    #         chunks = []
    #         total_bytes = 0
    #
    #         try:
    #             async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB/块
    #                 if not chunk:
    #                     continue
    #                 chunks.append(chunk)
    #                 total_bytes += len(chunk)
    #
    #         except (aiohttp.ClientPayloadError, aiohttp.ClientSSLError) as e:
    #             if not chunks:
    #                 # 完全没收到数据，抛出让上层重试
    #                 raise
    #
    #             if content_length:
    #                 ratio = total_bytes / int(content_length)
    #                 if ratio < 0.99:
    #                     # 缺失超过 1%，抛出让上层重试
    #                     raise ValueError(f"[aiohttp] payload 不完整 完整度 {ratio:.2f}，重试: {url}")
    #                 print(f"[aiohttp] payload 轻微不完整({ratio:.1%})，视为成功: {url}")
    #             else:
    #                 # chunked 传输无 Content-Length，有数据就用， 实际测试发现 不是这样的 文件打不开
    #                 # 但是 jstage 这个 都是没有  Content-Length 有点没办法了 jstage 就是不行的 这样的是不对的 文件打不开
    #                 print(f"[aiohttp] chunked 提前结束，已收 {total_bytes} bytes，缺失 content length 视为成功: {url}")
    #                 return None
    #                 # return b"".join(chunks), resp_headers
    #
    #         return b"".join(chunks), resp_headers
    #
    #
    #
    #
    #     # ── 改写：curl_cffi 异步单文件处理 ────────────────────────────────────────────
    async def _smart_download_async(
            self,
            session: aiohttp.ClientSession,
            url: str,
            proxy: str | None = None,
            sock_read: int = 300,
    ) -> tuple[bytes, dict]:
        """
        核心下载方法：
        - 使用 resp.read() 一次性读取，避免流式丢数据
        - Content-Length 校验完整性
        - proxy 作为参数传入，避免多协程竞态
        """
        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=30,
            sock_connect=30,
            sock_read=sock_read,
        )

        async with session.get(url, timeout=timeout, proxy=proxy, **self.kwargs) as resp:
            resp.raise_for_status()
            resp_headers = dict(resp.headers)
            content_length = resp_headers.get("Content-Length")
            try:
                content = await resp.read()
            except aiohttp.ClientPayloadError as e:
                # read() 中途断开，判断已缓冲的数据
                content = resp.content._buffer and b"".join(resp.content._buffer) or b""
                if not content:
                    raise
                if content_length:
                    ratio = len(content) / int(content_length)
                    if ratio < 0.99:
                        raise ValueError(f"payload 不完整({ratio:.1%})，触发重试: {url}")
                    print(f"[aiohttp] payload 轻微不完整({ratio:.1%})，视为成功: {url}")
                else:
                    raise  # 无 Content-Length 且中途断开，无法判断完整性，直接重试

            # 正常完成后也做完整性校验
            if content_length:
                expected = int(content_length)
                actual = len(content)
                if actual < expected:
                    ratio = actual / expected
                    raise ValueError(f"[aiohttp] 数据不完整({ratio:.1%}) 期望{expected}bytes 实际{actual}bytes: {url}")

            return content, resp_headers

    async def _handle_one_file_async_curl(
                self,
                id_url: dict,
                sem: asyncio.Semaphore,
        ) -> dict | bool | None:
            async with sem:
                file_url = ""
                try:
                    from curl_cffi.requests import AsyncSession
                    from curl_cffi import CurlOpt

                    file_url = id_url.get(self.file_url_field, "")
                    if not file_url.startswith(("http://", "https://")):
                        return False

                    retry_count = 3
                    for retry in range(retry_count):
                        try:
                            async with AsyncSession(
                                    headers=self.headers,
                                    impersonate="chrome146",
                                    proxies=(
                                            {"https": self.proxies, "http": self.proxies}
                                            if self.proxies else None
                                    ),
                            ) as session:
                                resp = await session.get(
                                    file_url,
                                    timeout=600,  # 兜底 10 分钟
                                    curl_options={
                                        # 速度低于 1KB/s 持续 30s 才断，正常传输绝不触发
                                        CurlOpt.LOW_SPEED_LIMIT: 1024,
                                        CurlOpt.LOW_SPEED_TIME: 30,
                                    },
                                )

                            if resp.status_code != 200:
                                wait = 2
                                await asyncio.sleep(wait)
                                if self.proxy_util:
                                    new_proxy = self.proxy_util.get_proxy()
                                    self.proxies = (
                                        new_proxy.get("http")
                                        if isinstance(new_proxy, dict)
                                        else new_proxy
                                    )
                                continue

                            content = resp.content
                            if not content:
                                await asyncio.sleep(2)
                                continue

                            resp_headers = dict(resp.headers)
                            file_path = self.get_file_path(
                                id=id_url.get("_id", ""),
                                url=file_url,
                                headers=resp_headers,
                                default_type=self.default_type,
                            )
                            with open(file_path, "wb") as f:
                                f.write(content)
                            id_url["file_path"] = file_path
                            return id_url

                        except Exception as e:
                            wait = 2 + retry % 3
                            print(
                                f"[curl_cffi] 异常: {file_url}, 重试({retry + 1}/{retry_count}), 等待{wait}s, 错误: {e}")
                            await asyncio.sleep(wait)

                    print(f"[curl_cffi] 超出重试次数，放弃: {file_url}")
                    return False

                except Exception as e:
                    print(f"[curl_cffi] 处理文件 {file_url} 时发生异常: {e}")
                    return False

